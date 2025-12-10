"""Filter pipeline for combining multiple filters."""

import logging
from typing import List, Tuple, Optional
from src.filters.base import BaseFilter
from src.filters.skill_filter import SkillFilter
from src.filters.budget_filter import BudgetFilter
from src.filters.blacklist_filter import BlacklistFilter
from src.models import Project

logger = logging.getLogger(__name__)


class FilterPipeline:
    """Pipeline that runs projects through multiple filters."""

    def __init__(self, filters: List[BaseFilter] = None):
        """Initialize the filter pipeline.

        Args:
            filters: List of filters to apply in order.
                    If None, creates default filters from settings.
        """
        if filters is None:
            self._filters = [
                SkillFilter(),
                BlacklistFilter(),
                BudgetFilter(),
            ]
        else:
            self._filters = filters

    def add_filter(self, filter_: BaseFilter) -> "FilterPipeline":
        """Add a filter to the pipeline."""
        self._filters.append(filter_)
        return self

    def passes(self, project: Project) -> bool:
        """Check if a project passes all filters."""
        return self.evaluate(project)[0]

    def evaluate(self, project: Project) -> Tuple[bool, Optional[str]]:
        """Evaluate a project against all filters.

        Returns:
            Tuple of (passed: bool, rejection_reason: Optional[str])
        """
        for filter_ in self._filters:
            if not filter_.passes(project):
                reason = filter_.get_rejection_reason(project)
                return False, reason
        return True, None

    def filter_projects(self, projects: List[Project]) -> List[Project]:
        """Filter a list of projects, returning only those that pass all filters.

        Args:
            projects: List of projects to filter.

        Returns:
            List of projects that passed all filters.
        """
        if not projects:
            return []

        suitable = []
        for project in projects:
            passed, reason = self.evaluate(project)
            if passed:
                suitable.append(project)
            else:
                logger.debug(f"Project {project.id} filtered: {reason}")

        logger.info(
            f"Filter pipeline: {len(suitable)}/{len(projects)} projects passed"
        )
        return suitable
