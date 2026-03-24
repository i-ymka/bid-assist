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
            "owner_info": "true",
        }

        logger.debug(f"Fetching active projects...")

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
                        logger.debug(f"Skipping project {p.get('id')} - preferred-only")
                        continue

                project = Project.from_api_response(p, users)
                projects.append(project)

            if should_skip_preferred:
                logger.debug(f"Fetched {len(projects)} active projects (filtered preferred-only)")
            else:
                logger.debug(f"Fetched {len(projects)} active projects (including preferred-only)")
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
            "owner_info": "true",
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
        """Fetch the project owner's country.

        First tries owner_info via auth-v2 (works even with 0 bids),
        then falls back to the bids endpoint.

        Args:
            project_id: The project ID to fetch owner country for.

        Returns:
            Country name string, or None if unable to determine.
        """
        # Try auth-v2 owner_info first (most reliable, works with 0 bids)
        try:
            owner_info = self._client.get_project_owner_info(project_id)
            if owner_info:
                country = owner_info.get("country", {}).get("name")
                if country:
                    logger.info(f"Project {project_id}: owner country = {country} (via owner_info)")
                    return country
        except Exception as e:
            logger.debug(f"owner_info fallback failed for {project_id}: {e}")

        # Fallback: bids endpoint (only works if project has bids)
        endpoint = PROJECT_BIDS_ENDPOINT.format(project_id=project_id)
        params = {
            "limit": 1,
            "user_details": "true",
            "user_country_details": "true",
        }

        try:
            logger.debug(f"Fetching owner country for project {project_id} via bids...")
            response = self._client.get(endpoint, params=params)
            result = response.get("result", {})
            bids = result.get("bids", [])

            if not bids:
                logger.info(f"Project {project_id}: No bids yet, cannot determine owner country")
                return None

            owner_id = bids[0].get("project_owner_id")
            if not owner_id:
                return None

            users = result.get("users", {})
            owner = users.get(str(owner_id), {})
            location = owner.get("location", {})
            country = location.get("country", {}).get("name")

            logger.info(f"Project {project_id}: owner country = {country} (via bids)")
            return country

        except Exception as e:
            logger.warning(f"Could not fetch owner country for project {project_id}: {e}")
            return None

    def get_project_owner_display_name(self, project_id: int) -> Optional[str]:
        """Fetch the project owner's display name (public_name) for bid greeting.

        Strategy:
        1. Fetch bids for project → get project_owner_id from first bid
        2. Fetch /users/0.1/users/{owner_id}/ → return public_name or display_name
        Falls back to None if bids endpoint has no bids or user lookup fails.

        Args:
            project_id: The project ID.

        Returns:
            Owner display name string, or None if unavailable.
        """
        try:
            # Step 1: get owner_id from bids endpoint
            endpoint = PROJECT_BIDS_ENDPOINT.format(project_id=project_id)
            response = self._client.get(endpoint, params={"limit": 1, "user_details": "true"})
            result = response.get("result", {})
            bids = result.get("bids", [])

            owner_id = None
            if bids:
                owner_id = bids[0].get("project_owner_id")

            # Also check users dict from bids response
            if owner_id:
                users = result.get("users", {})
                user = users.get(str(owner_id)) or users.get(owner_id)
                if user:
                    name = user.get("public_name") or user.get("display_name")
                    if name:
                        logger.debug(f"Project {project_id}: owner display_name='{name}' (from bids users dict)")
                        return name

            # Step 2: fetch user profile directly
            if owner_id:
                user_response = self._client.get(
                    f"/users/0.1/users/{owner_id}/",
                    params={"compact": "true"},
                )
                user_result = user_response.get("result", {})
                name = user_result.get("public_name") or user_result.get("display_name")
                if name:
                    logger.debug(f"Project {project_id}: owner display_name='{name}' (from user profile)")
                    return name

            logger.debug(f"Project {project_id}: owner display_name not found")
            return None

        except Exception as e:
            logger.debug(f"get_project_owner_display_name({project_id}) failed: {e}")
            return None

    def get_portfolio_count(self, user_id: int) -> Optional[int]:
        """Fetch portfolio item count for a user.

        Args:
            user_id: Freelancer user ID.

        Returns:
            Total portfolio count, or None if the API doesn't support it or request fails.
        """
        try:
            response = self._client.get(
                "/users/0.1/portfolios/",
                params={"users[]": user_id, "compact": "true", "limit": 0},
            )
            result = response.get("result", {})
            return result.get("total_count")
        except Exception as e:
            logger.debug(f"get_portfolio_count({user_id}) failed: {e}")
            return None
