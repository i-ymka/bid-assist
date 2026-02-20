"""API route handlers for Custom GPT integration."""

import logging
from fastapi import APIRouter, HTTPException

from src.api.schemas import (
    NextProjectResponse,
    ProjectResponse,
    DecisionRequest,
    DecisionResponse,
    StatusResponse,
    VerdictEnum,
)
from src.services.storage import ProjectRepository
from src.services.telegram.notifier import Notifier

logger = logging.getLogger(__name__)

router = APIRouter()

# Singleton instances
_repository = None
_notifier = None


def get_repository() -> ProjectRepository:
    global _repository
    if _repository is None:
        _repository = ProjectRepository()
    return _repository


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier


@router.get("/next_project", response_model=NextProjectResponse)
async def get_next_project():
    """Get the next pending project from the queue.

    Returns the freshest unprocessed project for GPT analysis.
    """
    repository = get_repository()

    # Get next pending project
    project_data = repository.get_next_from_queue()

    if not project_data:
        return NextProjectResponse(project=None)

    # Mark as sent to GPT
    repository.mark_queue_status(project_data["project_id"], "sent_to_gpt")

    # Format budget string
    budget_min = project_data["budget_min"]
    budget_max = project_data["budget_max"]
    currency = project_data["currency"] or "USD"
    if budget_min and budget_max:
        budget_str = f"${budget_min:.0f} - ${budget_max:.0f} {currency}"
    elif budget_max:
        budget_str = f"Up to ${budget_max:.0f} {currency}"
    else:
        budget_str = "Not specified"

    # Format simplified response for GPT
    project = ProjectResponse(
        project_id=project_data["project_id"],
        title=project_data["title"] or "",
        description=project_data["description"] or "",  # Full description
        budget=budget_str,
    )

    logger.info(f"Sent project {project.project_id} to GPT: {project.title[:50]}...")
    return NextProjectResponse(project=project)


@router.post("/submit_decision", response_model=DecisionResponse)
async def submit_decision(decision: DecisionRequest):
    """Submit GPT's analysis decision.

    If verdict is BID, sends notification to Telegram with Place Bid button.
    If verdict is SKIP, just marks project as processed.
    """
    repository = get_repository()
    notifier = get_notifier()

    project_id = decision.project_id

    # Get project data from queue
    # Note: We need to fetch it to get title, currency, etc. for notification
    cursor = repository._conn.cursor()
    cursor.execute(
        "SELECT * FROM project_queue WHERE project_id = ?",
        (project_id,),
    )
    row = cursor.fetchone()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Project {project_id} not found in queue"
        )

    project_data = dict(row)

    if decision.verdict == VerdictEnum.SKIP:
        # Send SKIP notification to Telegram (so user sees what was skipped)
        try:
            await notifier.send_skip_notification(
                project_id=project_id,
                title=project_data["title"],
                budget_min=project_data["budget_min"],
                budget_max=project_data["budget_max"],
                currency=project_data["currency"] or "USD",
                client_country=project_data["client_country"],
                url=project_data["url"],
                summary=decision.summary,
            )
        except Exception as e:
            logger.error(f"Failed to send SKIP notification: {e}")

        repository.mark_queue_status(project_id, "processed")
        repository.add_processed_project(project_id)
        logger.info(f"Project {project_id} skipped by GPT: {decision.summary[:100]}")
        return DecisionResponse(
            success=True,
            message="Project skipped, notification sent"
        )

    # Verdict is BID - send to Telegram
    bid_amount = decision.amount or project_data["budget_max"] or 100
    currency = project_data["currency"] or "USD"

    # Store pending bid data in database for when user clicks the button
    repository.add_pending_bid(
        project_id=project_id,
        amount=bid_amount,
        period=decision.period,
        description=decision.bid_text,
        title=project_data["title"],
        currency=currency,
    )

    # Send notification to Telegram
    try:
        sent = await notifier.send_gpt_decision_notification(
            project_id=project_id,
            title=project_data["title"],
            budget_min=project_data["budget_min"],
            budget_max=project_data["budget_max"],
            currency=currency,
            client_country=project_data["client_country"],
            bid_count=project_data["bid_count"] or 0,
            avg_bid=project_data["avg_bid"],
            url=project_data["url"],
            summary=decision.summary,
            bid_text=decision.bid_text,
            suggested_amount=bid_amount,
            suggested_period=decision.period,
        )

        if sent:
            repository.mark_queue_status(project_id, "processed")
            repository.add_processed_project(project_id)
            logger.info(f"Project {project_id} sent to Telegram for bid confirmation")
            return DecisionResponse(
                success=True,
                message="Sent to Telegram for confirmation"
            )
        else:
            return DecisionResponse(
                success=False,
                message="Failed to send Telegram notification"
            )

    except Exception as e:
        logger.error(f"Error sending notification for project {project_id}: {e}")
        return DecisionResponse(
            success=False,
            message=f"Error: {str(e)}"
        )


@router.get("/status", response_model=StatusResponse)
async def get_status():
    """Get current system status and statistics."""
    repository = get_repository()

    bid_stats = repository.get_bid_stats()

    return StatusResponse(
        queue_pending=repository.get_queue_count("pending"),
        queue_sent=repository.get_queue_count("sent_to_gpt"),
        queue_processed=repository.get_queue_count("processed"),
        projects_processed=repository.get_processed_count(),
        bids_total=bid_stats["bids_placed"],
        bids_successful=bid_stats["bids_placed"],
    )
