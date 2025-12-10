"""Filter projects by blacklisted keywords."""

import logging
from typing import List, Optional
from src.filters.base import BaseFilter
from src.models import Project
from src.config import settings

logger = logging.getLogger(__name__)


class BlacklistFilter(BaseFilter):
    """Filter out projects containing blacklisted keywords."""

    def __init__(self, blacklist_keywords: List[str] = None):
        """Initialize the blacklist filter.

        Args:
            blacklist_keywords: List of keywords to reject.
                              If None, uses settings.blacklist_keywords.
        """
        self._blacklist = (
            [kw.lower() for kw in blacklist_keywords]
            if blacklist_keywords is not None
            else settings.blacklist_keywords
        )
        self._matched_keyword: Optional[str] = None

    @property
    def name(self) -> str:
        return "BlacklistFilter"

    def passes(self, project: Project) -> bool:
        """Check if project contains any blacklisted keywords."""
        if not self._blacklist:
            return True  # No blacklist = accept all

        project_text = f"{project.title} {project.description}".lower()

        for keyword in self._blacklist:
            if keyword and keyword in project_text:
                self._matched_keyword = keyword
                logger.debug(
                    f"Project {project.id}: Blacklisted keyword '{keyword}' found"
                )
                return False

        self._matched_keyword = None
        return True

    def get_rejection_reason(self, project: Project) -> Optional[str]:
        if self.passes(project):
            return None
        return f"Contains blacklisted keyword: '{self._matched_keyword}'"
