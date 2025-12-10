"""Bid and AI analysis data models."""

from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum


class Difficulty(str, Enum):
    """Project difficulty levels."""

    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"
    UNKNOWN = "UNKNOWN"


class AIAnalysis(BaseModel):
    """AI analysis result for a project."""

    difficulty: Difficulty = Difficulty.UNKNOWN
    summary: str = ""
    suggested_bid_text: str = ""
    suggested_amount: Optional[float] = None
    suggested_period: Optional[int] = None

    @classmethod
    def from_ai_response(
        cls, rating: str, summary: str, bid_text: str
    ) -> "AIAnalysis":
        """Create AIAnalysis from AI response strings."""
        try:
            difficulty = Difficulty(rating.upper().strip())
        except ValueError:
            difficulty = Difficulty.UNKNOWN

        return cls(
            difficulty=difficulty,
            summary=summary,
            suggested_bid_text=bid_text,
        )


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
