"""Base filter class for project filtering."""

from abc import ABC, abstractmethod
from typing import Optional
from src.models import Project


class BaseFilter(ABC):
    """Abstract base class for project filters."""

    @abstractmethod
    def passes(self, project: Project) -> bool:
        """Check if a project passes this filter.

        Args:
            project: The project to check.

        Returns:
            True if the project passes the filter, False otherwise.
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the name of this filter."""
        pass

    def get_rejection_reason(self, project: Project) -> Optional[str]:
        """Get the reason why a project was rejected.

        Args:
            project: The project that was rejected.

        Returns:
            A string describing why the project was rejected, or None if it passed.
        """
        if self.passes(project):
            return None
        return f"Rejected by {self.name}"
