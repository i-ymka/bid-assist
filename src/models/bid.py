"""Bid and AI analysis data models."""

from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum


class Verdict(str, Enum):
    """AI verdict for whether to bid on a project."""

    BID = "BID"
    SKIP = "SKIP"
    UNKNOWN = "UNKNOWN"


# Alias for backwards compatibility
Difficulty = Verdict


class AIAnalysis(BaseModel):
    """AI analysis result for a project."""

    verdict: Verdict = Verdict.UNKNOWN
    summary: str = ""
    suggested_bid_text: str = ""
    suggested_amount: Optional[float] = None
    suggested_period: Optional[int] = None

    @property
    def should_bid(self) -> bool:
        """Check if we should bid on this project."""
        return self.verdict == Verdict.BID

    @property
    def difficulty(self) -> Verdict:
        """Alias for backwards compatibility."""
        return self.verdict


class Bid(BaseModel):
    """Bid to be placed on a project."""

    project_id: int
    amount: float
    period: int = 3  # days
    milestone_percentage: int = 100
    description: str = ""


class BidResult(BaseModel):
    """Result of a bid placement attempt."""

    success: bool
    message: str
    bid_id: Optional[int] = None
    error_code: Optional[str] = None
