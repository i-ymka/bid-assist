"""Project data models."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class ProjectBudget(BaseModel):
    """Project budget information."""

    minimum: float = 0
    maximum: float = 0

    @field_validator("minimum", "maximum", mode="before")
    @classmethod
    def convert_none_to_zero(cls, v):
        """Convert None to 0 for budget fields."""
        return v if v is not None else 0


class ProjectOwner(BaseModel):
    """Project owner information."""

    id: int = 0
    username: str = "N/A"
    country: str = "Unknown"


class ProjectSkill(BaseModel):
    """Skill/job associated with a project."""

    id: int
    name: str = ""


class ProjectCurrency(BaseModel):
    """Currency information."""

    code: str = "USD"
    name: str = "US Dollar"


class BidStats(BaseModel):
    """Bid statistics for a project."""

    bid_count: int = 0
    bid_avg: Optional[float] = None


class Project(BaseModel):
    """Freelancer project model."""

    id: int
    title: str = ""
    description: str = ""
    budget: ProjectBudget = Field(default_factory=ProjectBudget)
    currency: ProjectCurrency = Field(default_factory=ProjectCurrency)
    owner: ProjectOwner = Field(default_factory=lambda: ProjectOwner(id=0))
    jobs: List[ProjectSkill] = Field(default_factory=list)
    status: str = ""
    type: str = ""
    winner_id: Optional[int] = None

    # New fields
    bid_stats: BidStats = Field(default_factory=BidStats)
    hireme: bool = False  # True = "hire me" project
    upgrades: dict = Field(default_factory=dict)  # Project upgrades (pf_only, featured, etc.)
    nda_required: bool = False
    nda_details: Optional[str] = None
    time_submitted: Optional[datetime] = None
    language: str = "en"  # Project language code (e.g., "en", "es", "de")

    @property
    def skill_ids(self) -> set:
        """Get set of skill IDs for this project."""
        return {job.id for job in self.jobs}

    @property
    def url(self) -> str:
        """Get direct URL to the project."""
        return f"https://www.freelancer.com/projects/{self.id}"

    @property
    def budget_str(self) -> str:
        """Get formatted budget string."""
        return f"{self.budget.minimum:.0f} - {self.budget.maximum:.0f} {self.currency.code}"

    @property
    def avg_bid_str(self) -> str:
        """Get formatted average bid string."""
        if self.bid_stats.bid_avg:
            return f"{self.bid_stats.bid_avg:.0f} {self.currency.code}"
        return "N/A"

    @property
    def is_preferred_only(self) -> bool:
        """Check if project is for preferred freelancers only."""
        return (
            self.hireme
            or self.upgrades.get("pf_only", False)
            or self.upgrades.get("preferred", False)
        )

    def is_older_than_hours(self, hours: float) -> bool:
        """Check if project is older than specified hours.

        Args:
            hours: Maximum age in hours.

        Returns:
            True if project is older than specified hours.
        """
        if not self.time_submitted:
            return False  # If no timestamp, don't filter it out
        age = datetime.utcnow() - self.time_submitted
        return age.total_seconds() > hours * 3600

    @classmethod
    def from_api_response(cls, data: dict, users: dict = None) -> "Project":
        """Create Project from Freelancer API response.

        Args:
            data: Project data from API
            users: Optional dict of users keyed by user ID (for owner details)
        """
        # Get owner info
        owner_id = data.get("owner_id") or 0
        owner_data = {"id": owner_id, "username": "N/A", "country": "Unknown"}

        if data.get("owner"):
            owner_obj = data["owner"]
            owner_data["id"] = owner_obj.get("id") or owner_id or 0
            owner_data["username"] = owner_obj.get("username", "N/A")
            # Check if location is directly on owner object
            location = owner_obj.get("location", {})
            if location:
                country = location.get("country", {})
                if country:
                    owner_data["country"] = country.get("name", "Unknown") or "Unknown"

        # Get country from users dict if available (overwrites if found)
        # API may key by string or int
        user = users.get(str(owner_id)) or users.get(owner_id) if users else None
        if user:
            owner_data["username"] = user.get("username", owner_data["username"])
            location = user.get("location", {})
            if location:
                country = location.get("country", {})
                if country:
                    owner_data["country"] = country.get("name", "Unknown") or "Unknown"

        # owner_info (returned when owner_info=true param is used)
        # Most reliable source for client country — overrides previous values
        owner_info = data.get("owner_info")
        if owner_info:
            oi_country = owner_info.get("country", {})
            if oi_country and oi_country.get("name"):
                owner_data["country"] = oi_country["name"]

        # Get bid stats
        bid_stats_data = data.get("bid_stats", {})
        bid_stats = BidStats(
            bid_count=bid_stats_data.get("bid_count", 0) or 0,
            bid_avg=bid_stats_data.get("bid_avg"),
        )

        # Check NDA
        nda_details = data.get("nda_details")
        nda_required = nda_details is not None and nda_details != {}

        # Parse time_submitted (Unix timestamp from API)
        time_submitted = None
        ts = data.get("time_submitted")
        if ts:
            try:
                time_submitted = datetime.utcfromtimestamp(ts)
            except (ValueError, TypeError):
                pass

        # Parse language (Freelancer API returns "language" as a code like "en")
        language = data.get("language") or "en"

        return cls(
            id=data.get("id", 0),
            title=data.get("title") or "",
            description=data.get("description") or "",
            budget=ProjectBudget(**(data.get("budget") or {})),
            currency=ProjectCurrency(**(data.get("currency") or {})),
            owner=ProjectOwner(**owner_data),
            jobs=[ProjectSkill(**job) for job in (data.get("jobs") or [])],
            status=data.get("status") or "",
            type=data.get("type") or "",
            winner_id=data.get("winner_id"),
            bid_stats=bid_stats,
            hireme=data.get("hireme") or False,
            upgrades=data.get("upgrades") or {},
            nda_required=nda_required,
            nda_details=str(nda_details) if nda_details else None,
            time_submitted=time_submitted,
            language=language,
        )
