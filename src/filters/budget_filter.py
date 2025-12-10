"""Filter projects by budget range."""

import logging
from typing import Optional
from src.filters.base import BaseFilter
from src.models import Project
from src.config import settings

logger = logging.getLogger(__name__)


class BudgetFilter(BaseFilter):
    """Filter projects within a budget range."""

    def __init__(self, min_budget: float = None, max_budget: float = None):
        """Initialize the budget filter.

        Args:
            min_budget: Minimum acceptable budget. If None, uses settings.min_budget.
            max_budget: Maximum acceptable budget. If None, uses settings.max_budget.
        """
        self._min_budget = min_budget if min_budget is not None else settings.min_budget
        self._max_budget = max_budget if max_budget is not None else settings.max_budget

    @property
    def name(self) -> str:
        return "BudgetFilter"

    @property
    def min_budget(self) -> float:
        return self._min_budget

    @property
    def max_budget(self) -> float:
        return self._max_budget

    def passes(self, project: Project) -> bool:
        """Check if project budget is within acceptable range."""
        project_max_budget = project.budget.maximum

        if not project_max_budget:
            logger.debug(f"Project {project.id}: No budget defined")
            return False

        in_range = self._min_budget <= project_max_budget <= self._max_budget

        if not in_range:
            logger.debug(
                f"Project {project.id}: Budget ${project_max_budget} "
                f"not in range ${self._min_budget}-${self._max_budget}"
            )

        return in_range

    def get_rejection_reason(self, project: Project) -> Optional[str]:
        if self.passes(project):
            return None
        return (
            f"Budget ${project.budget.maximum} outside range "
            f"${self._min_budget}-${self._max_budget}"
        )
