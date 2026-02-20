"""Filter projects by client country."""

import logging
from typing import List, Optional
from src.filters.base import BaseFilter
from src.models import Project
from src.config import settings

logger = logging.getLogger(__name__)


class CountryFilter(BaseFilter):
    """Filter projects by client country (whitelist or blacklist)."""

    def __init__(
        self,
        allowed_countries: List[str] = None,
        blocked_countries: List[str] = None,
        block_unknown: bool = None,
    ):
        """Initialize the country filter.

        Args:
            allowed_countries: List of allowed country names (whitelist).
                              If provided, only these countries are accepted.
                              If None, uses settings.allowed_countries.
            blocked_countries: List of blocked country names (blacklist).
                              If None, uses settings.blocked_countries.
            block_unknown: If True, block projects with unknown country.
                          If None, uses settings.block_unknown_countries.

        Note: If allowed_countries is set, blocked_countries is ignored.
        """
        self._allowed = (
            [c.lower().strip() for c in allowed_countries]
            if allowed_countries is not None
            else settings.allowed_countries
        )
        self._blocked = (
            [c.lower().strip() for c in blocked_countries]
            if blocked_countries is not None
            else settings.blocked_countries
        )
        self._block_unknown = (
            block_unknown
            if block_unknown is not None
            else getattr(settings, 'block_unknown_countries', False)
        )
        self._rejection_reason: Optional[str] = None

    @property
    def name(self) -> str:
        return "CountryFilter"

    def passes(self, project: Project) -> bool:
        """Check if project's client country passes the filter."""
        country = (project.owner.country or "").lower().strip()

        # Handle unknown country
        if not country or country == "unknown":
            if self._block_unknown:
                self._rejection_reason = "Country is unknown (blocked by settings)"
                logger.debug(f"Project {project.id}: {self._rejection_reason}")
                return False
            return True

        # Whitelist mode: only allow specific countries
        if self._allowed:
            if country in self._allowed:
                return True
            self._rejection_reason = f"Country '{project.owner.country}' not in allowed list"
            logger.debug(f"Project {project.id}: {self._rejection_reason}")
            return False

        # Blacklist mode: block specific countries
        if self._blocked and country in self._blocked:
            self._rejection_reason = f"Country '{project.owner.country}' is blocked"
            logger.debug(f"Project {project.id}: {self._rejection_reason}")
            return False

        return True

    def get_rejection_reason(self, project: Project) -> Optional[str]:
        if self.passes(project):
            return None
        return self._rejection_reason
