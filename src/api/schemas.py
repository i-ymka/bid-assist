"""Pydantic schemas for API request/response models."""

from typing import Optional
from pydantic import BaseModel, Field
from enum import Enum


class VerdictEnum(str, Enum):
    """GPT verdict for whether to bid on a project."""
    BID = "BID"
    SKIP = "SKIP"


class ProjectResponse(BaseModel):
    """Project data returned from /next_project (simplified for GPT)."""
    project_id: int  # Keep for submit_decision reference
    title: str
    description: str  # Full description, no cutting
    budget: str  # Formatted: "$100 - $250 USD"


class NextProjectResponse(BaseModel):
    """Response for GET /next_project."""
    project: Optional[ProjectResponse] = None


class DecisionRequest(BaseModel):
    """Request body for POST /submit_decision."""
    project_id: int
    verdict: VerdictEnum
    summary: str = Field(..., description="Brief summary of the analysis")
    bid_text: str = Field(default="", description="Proposal text for the bid")
    amount: Optional[float] = Field(default=None, description="Bid amount")
    period: int = Field(default=3, description="Delivery period in days")


class DecisionResponse(BaseModel):
    """Response for POST /submit_decision."""
    success: bool
    message: str


class StatusResponse(BaseModel):
    """Response for GET /status."""
    queue_pending: int
    queue_sent: int
    queue_processed: int
    projects_processed: int
    bids_total: int
    bids_successful: int
