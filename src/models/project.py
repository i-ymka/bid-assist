"""Project data models."""

from typing import List, Optional
from pydantic import BaseModel, Field


class ProjectBudget(BaseModel):
    """Project budget information."""

    minimum: float = 0
    maximum: float = 0


class ProjectOwner(BaseModel):
    """Project owner information."""

    id: int
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

    # New fields
    bid_stats: BidStats = Field(default_factory=BidStats)
    hireme: bool = False  # True = preferred freelancer only
    nda_required: bool = False
    nda_details: Optional[str] = None

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
        return self.hireme

    @classmethod
    def from_api_response(cls, data: dict, users: dict = None) -> "Project":
        """Create Project from Freelancer API response.

        Args:
            data: Project data from API
            users: Optional dict of users keyed by user ID (for owner details)
        """
        # Get owner info
        owner_id = data.get("owner_id", 0)
        owner_data = {"id": owner_id, "username": "N/A", "country": "Unknown"}

        if data.get("owner"):
            owner_data["id"] = data["owner"].get("id", owner_id)
            owner_data["username"] = data["owner"].get("username", "N/A")

        # Get country from users dict if available
        if users and str(owner_id) in users:
            user = users[str(owner_id)]
            owner_data["username"] = user.get("username", owner_data["username"])
            location = user.get("location", {})
            if location:
                country = location.get("country", {})
                if country:
                    owner_data["country"] = country.get("name", "Unknown") or "Unknown"

        # Get bid stats
        bid_stats_data = data.get("bid_stats", {})
        bid_stats = BidStats(
            bid_count=bid_stats_data.get("bid_count", 0) or 0,
            bid_avg=bid_stats_data.get("bid_avg"),
        )

        # Check NDA
        nda_details = data.get("nda_details")
        nda_required = nda_details is not None and nda_details != {}

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
            bid_stats=bid_stats,
            hireme=data.get("hireme") or False,
            nda_required=nda_required,
            nda_details=str(nda_details) if nda_details else None,
        )
