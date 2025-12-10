"""Filter projects by required skills."""

import logging
from typing import Set
from src.filters.base import BaseFilter
from src.models import Project
from src.config import settings

logger = logging.getLogger(__name__)


class SkillFilter(BaseFilter):
    """Filter projects that have at least one required skill."""

    def __init__(self, required_skill_ids: Set[int] = None):
        """Initialize the skill filter.

        Args:
            required_skill_ids: Set of skill IDs to match against.
                              If None, uses settings.skill_ids.
        """
        self._required_skills = (
            set(required_skill_ids)
            if required_skill_ids is not None
            else set(settings.skill_ids)
        )

    @property
    def name(self) -> str:
        return "SkillFilter"

    def passes(self, project: Project) -> bool:
        """Check if project has at least one required skill."""
        if not self._required_skills:
            return True  # No required skills = accept all

        if not project.jobs:
            logger.debug(f"Project {project.id}: No skills defined")
            return False

        project_skill_ids = project.skill_ids
        has_match = bool(self._required_skills.intersection(project_skill_ids))

        if not has_match:
            logger.debug(
                f"Project {project.id}: No matching skills. "
                f"Project has: {project_skill_ids}, Required: {self._required_skills}"
            )

        return has_match

    def get_rejection_reason(self, project: Project) -> str:
        if self.passes(project):
            return None
        return f"No matching skills (required: {len(self._required_skills)} skill IDs)"
