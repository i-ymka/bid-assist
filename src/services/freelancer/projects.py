"""Project fetching service for Freelancer API."""

import logging
from typing import List, Optional
from src.services.freelancer.client import FreelancerClient
from src.models import Project
from src.config import settings
from src.config.constants import PROJECTS_ACTIVE_ENDPOINT, PROJECT_DETAILS_ENDPOINT, PROJECT_BIDS_ENDPOINT

logger = logging.getLogger(__name__)


class ProjectService:
    """Service for fetching projects from Freelancer API."""

    def __init__(self, client: FreelancerClient = None):
        """Initialize the project service.

        Args:
            client: FreelancerClient instance. If None, creates a new one.
        """
        self._client = client or FreelancerClient()

    def get_active_projects(
        self,
        skill_ids: List[int] = None,
        min_budget: float = None,
        limit: int = 50,
    ) -> List[Project]:
        """Fetch list of active projects from Freelancer.

        Args:
            skill_ids: Filter by skill IDs. If None, uses settings.skill_ids.
            min_budget: Minimum budget filter. If None, uses default (50).
            limit: Maximum number of projects to fetch.

        Returns:
            List of Project objects with bid stats and owner info.
        """
        skill_ids = skill_ids if skill_ids is not None else settings.skill_ids
        min_budget = min_budget if min_budget is not None else 50  # Default: $50

        params = {
            "jobs[]": skill_ids,
            "project_types[]": "fixed",
            "min_budget": min_budget,
            "limit": limit,
            # Request additional details
            "full_description": "true",
            "user_details": "true",
            "user_country_details": "true",
            "bid_details": "true",
            "upgrade_details": "true",
        }

        logger.info(f"Fetching active projects...")

        try:
            response = self._client.get(PROJECTS_ACTIVE_ENDPOINT, params=params)
            result = response.get("result", {})
            projects_data = result.get("projects", [])
            users = result.get("users", {})

            # Check runtime setting for skip_preferred_only
            from src.services.storage import ProjectRepository
            repo = ProjectRepository()
            should_skip_preferred = repo.skip_preferred_only()

            projects = []
            for p in projects_data:
                # Skip preferred freelancer only projects (if enabled in runtime settings)
                if should_skip_preferred:
                    upgrades = p.get("upgrades") or {}
                    is_preferred = (
                        p.get("hireme", False)
                        or upgrades.get("pf_only", False)
                        or upgrades.get("preferred", False)
                    )
                    if is_preferred:
                        logger.info(f"Skipping project {p.get('id')} - preferred freelancer only (upgrades: {upgrades})")
                        continue

                project = Project.from_api_response(p, users)
                projects.append(project)

            if should_skip_preferred:
                logger.info(f"Fetched {len(projects)} active projects (filtered preferred-only)")
            else:
                logger.info(f"Fetched {len(projects)} active projects (including preferred-only)")
            return projects

        except Exception as e:
            logger.error(f"Failed to fetch active projects: {e}")
            return []

    def get_project_details(self, project_id: int) -> Optional[Project]:
        """Fetch full details for a specific project.

        Args:
            project_id: The project ID to fetch.

        Returns:
            Project with full details, or None if fetch failed.
        """
        endpoint = PROJECT_DETAILS_ENDPOINT.format(project_id=project_id)
        params = {
            "full_description": "true",
            "job_details": "true",
            "user_details": "true",
            "user_country_details": "true",
            "bid_details": "true",
        }

        logger.debug(f"Fetching details for project {project_id}")

        try:
            response = self._client.get(endpoint, params=params)
            result = response.get("result")

            if not result:
                logger.warning(f"No data returned for project {project_id}")
                return None

            # For single project endpoint, users dict can be at different locations
            # Try response level first, then result level
            users = response.get("users", {}) or result.get("users", {})

            project = Project.from_api_response(result, users)

            # Fallback for country if not found
            if project.owner.country == "Unknown":
                logger.debug(f"Project {project_id}: Country is Unknown, trying fallback")
                country = self.get_project_owner_country(project_id)
                if country:
                    project.owner.country = country

            # Skip if preferred freelancer only (if enabled in runtime settings)
            from src.services.storage import ProjectRepository
            repo = ProjectRepository()
            if repo.skip_preferred_only() and project.is_preferred_only:
                logger.info(f"Skipping project {project_id} - preferred freelancer only")
                return None

            return project

        except Exception as e:
            logger.error(f"Failed to fetch project {project_id} details: {e}")
            return None

    def get_project_bids(self, project_id: int) -> (List[dict], dict):
        """Fetch all bids for a specific project.

        Args:
            project_id: The project ID.

        Returns:
            A tuple containing the list of bid dictionaries and the users dictionary from the API.
        """
        endpoint = PROJECT_BIDS_ENDPOINT.format(project_id=project_id)
        params = {
            "user_details": "true",
            "user_country_details": "true", # Ensure we get country details for bidders
        }

        logger.debug(f"Fetching all bids for project {project_id}")

        try:
            response = self._client.get(endpoint, params=params)
            result = response.get("result", {})
            bids = result.get("bids", [])
            users = result.get("users", {})
            return bids, users
        except Exception as e:
            logger.error(f"Failed to fetch bids for project {project_id}: {e}")
            return [], {}

    def get_project_owner_country(self, project_id: int) -> Optional[str]:
        """Fetch the project owner's country from the bids endpoint.

        The active projects endpoint doesn't return owner_id, so we need to
        fetch it from the bids endpoint where project_owner_id is available.

        Args:
            project_id: The project ID to fetch owner country for.

        Returns:
            Country name string, or None if unable to determine.
        """
        endpoint = PROJECT_BIDS_ENDPOINT.format(project_id=project_id)
        params = {
            "limit": 1,
            "user_details": "true",
            "user_country_details": "true",
        }

        try:
            logger.debug(f"Fetching owner country for project {project_id}...")
            response = self._client.get(endpoint, params=params)
            result = response.get("result", {})
            bids = result.get("bids", [])
            logger.debug(f"Project {project_id}: got {len(bids)} bids")

            if not bids:
                logger.info(f"Project {project_id}: No bids yet, cannot determine owner country")
                return None

            owner_id = bids[0].get("project_owner_id")
            logger.debug(f"Project {project_id}: owner_id = {owner_id}")
            if not owner_id:
                logger.warning(f"Project {project_id}: bid exists but no project_owner_id")
                return None

            users = result.get("users", {})
            owner = users.get(str(owner_id), {})
            location = owner.get("location", {})
            country = location.get("country", {}).get("name")

            logger.info(f"Project {project_id}: owner country = {country}")
            return country

        except Exception as e:
            logger.warning(f"Could not fetch owner country for project {project_id}: {e}")
            return None
