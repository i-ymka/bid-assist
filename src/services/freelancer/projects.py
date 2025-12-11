"""Project fetching service for Freelancer API."""

import logging
from typing import List, Optional
from src.services.freelancer.client import FreelancerClient
from src.models import Project
from src.config import settings
from src.config.constants import PROJECTS_ACTIVE_ENDPOINT, PROJECT_DETAILS_ENDPOINT

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
            min_budget: Minimum budget filter. If None, uses settings.min_budget.
            limit: Maximum number of projects to fetch.

        Returns:
            List of Project objects with bid stats and owner info.
        """
        skill_ids = skill_ids if skill_ids is not None else settings.skill_ids
        min_budget = min_budget if min_budget is not None else settings.min_budget

        params = {
            "jobs[]": skill_ids,
            "project_types[]": "fixed",
            "min_budget": min_budget,
            "limit": limit,
            # Request additional details
            "user_details": "true",
            "user_country_details": "true",
            "bid_details": "true",
        }

        logger.info(f"Fetching active projects...")

        try:
            response = self._client.get(PROJECTS_ACTIVE_ENDPOINT, params=params)
            result = response.get("result", {})
            projects_data = result.get("projects", [])
            users = result.get("users", {})

            projects = []
            for p in projects_data:
                # Skip preferred freelancer only projects
                if p.get("hireme", False):
                    logger.debug(f"Skipping project {p.get('id')} - preferred freelancer only")
                    continue

                projects.append(Project.from_api_response(p, users))

            logger.info(f"Fetched {len(projects)} active projects (filtered preferred-only)")
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

            # For single project, result is the project itself
            # Users might be in a separate key or embedded
            users = response.get("users", {})

            project = Project.from_api_response(result, users)

            # Skip if preferred freelancer only
            if project.is_preferred_only:
                logger.info(f"Skipping project {project_id} - preferred freelancer only")
                return None

            return project

        except Exception as e:
            logger.error(f"Failed to fetch project {project_id} details: {e}")
            return None
