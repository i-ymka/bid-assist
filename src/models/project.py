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


class ProjectSkill(BaseModel):
    """Skill/job associated with a project."""

    id: int
    name: str = ""


class ProjectCurrency(BaseModel):
    """Currency information."""

    code: str = "USD"
    name: str = "US Dollar"


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
        return f"{self.budget.minimum} - {self.budget.maximum} {self.currency.code}"

    @classmethod
    def from_api_response(cls, data: dict) -> "Project":
        """Create Project from Freelancer API response."""
        return cls(
            id=data.get("id", 0),
            title=data.get("title", ""),
            description=data.get("description", ""),
            budget=ProjectBudget(**data.get("budget", {})),
            currency=ProjectCurrency(**data.get("currency", {})),
            owner=ProjectOwner(**data.get("owner", {})) if data.get("owner") else ProjectOwner(id=0),
            jobs=[ProjectSkill(**job) for job in data.get("jobs", [])],
            status=data.get("status", ""),
            type=data.get("type", ""),
        )
