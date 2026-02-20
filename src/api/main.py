"""FastAPI application with automated Gemini analysis.

This module:
1. Polls Freelancer for new projects
2. Automatically analyzes them using Gemini CLI
3. Sends notifications to Telegram for bid confirmation

No external API calls needed - uses local Gemini CLI.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.config import settings
from src.services.freelancer import FreelancerClient, ProjectService
from src.services.storage import ProjectRepository
from src.services.telegram.notifier import Notifier
from src.services.ai.gemini_analyzer import analyze_project
from src.filters import FilterPipeline, BudgetFilter, CountryFilter

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Background task references
_polling_task = None
_analysis_task = None


async def polling_loop():
    """Background polling loop that fetches projects from Freelancer.

    Runs every POLL_INTERVAL seconds and:
    1. Fetches active projects from Freelancer API
    2. Filters by skills, budget, country, age
    3. Stores passing projects in the queue for GPT processing
    """
    logger.info("Background polling loop started")

    # Initialize services
    client = FreelancerClient()
    project_service = ProjectService(client)
    repository = ProjectRepository()

    while True:
        try:
            # Get dynamic poll interval from database
            poll_interval = repository.get_poll_interval()

            # Check if paused (from database)
            if repository.is_paused():
                logger.debug("Monitoring is paused, skipping cycle")
                await asyncio.sleep(poll_interval)
                continue

            logger.info(f"--- Starting polling cycle (interval: {poll_interval}s) ---")

            # Cleanup old projects from queue
            cleaned = repository.cleanup_old_queue_items(settings.max_project_age_hours)
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} stale projects from queue")

            # Fetch projects from Freelancer
            projects = project_service.get_active_projects()
            if not projects:
                logger.info("No projects found")
                await asyncio.sleep(poll_interval)
                continue

            # Create filter pipeline
            filter_pipeline = FilterPipeline([
                CountryFilter(),
                BudgetFilter(
                    min_budget=50,  # Default: $50
                    max_budget=3000,  # Default: $3000
                ),
            ])

            added_count = 0
            for project_preview in projects:
                project_id = project_preview.id

                # Skip if already processed or in queue
                if repository.is_processed(project_id):
                    continue
                if repository.is_in_queue(project_id):
                    continue

                # Skip old projects
                if project_preview.is_older_than_hours(settings.max_project_age_hours):
                    logger.debug(f"Project {project_id} too old, skipping")
                    repository.add_processed_project(project_id)
                    continue

                # Get full project details
                project = project_service.get_project_details(project_id)
                if not project:
                    repository.add_processed_project(project_id)
                    continue

                # Skip if too many bids already
                if project.bid_stats.bid_count > settings.max_bid_count:
                    logger.info(f"Project {project_id} has {project.bid_stats.bid_count} bids (max: {settings.max_bid_count}), skipping")
                    repository.add_processed_project(project_id)
                    continue

                # Apply filters
                passed, reason = filter_pipeline.evaluate(project)
                if not passed:
                    logger.debug(f"Project {project_id} filtered: {reason}")
                    repository.add_processed_project(project_id)
                    continue

                # Add to queue for GPT processing
                repository.add_to_queue(
                    project_id=project.id,
                    title=project.title,
                    description=project.description,
                    budget_min=project.budget.minimum,
                    budget_max=project.budget.maximum,
                    currency=project.currency.code,
                    client_country=project.owner.country,
                    bid_count=project.bid_stats.bid_count,
                    avg_bid=project.bid_stats.bid_avg,
                    url=project.url,
                    time_submitted=project.time_submitted,
                )
                added_count += 1
                logger.info(f"Added project {project_id} to queue: {project.title[:50]}...")

            pending_count = repository.get_queue_count("pending")
            logger.info(f"Polling cycle complete: {added_count} new projects added, {pending_count} pending in queue")

        except Exception as e:
            logger.error(f"Error in polling cycle: {e}")

        await asyncio.sleep(poll_interval)


async def analysis_loop():
    """Background analysis loop that processes projects using Gemini CLI.

    Takes projects from the queue, analyzes them, and sends notifications.
    """
    logger.info("Background analysis loop started")

    repository = ProjectRepository()
    notifier = Notifier()

    # Wait a bit for polling to start first
    await asyncio.sleep(10)

    while True:
        try:
            # Check if paused (from database - shared with Telegram bot)
            if repository.is_paused():
                await asyncio.sleep(5)
                continue

            # Get next pending project
            project_data = repository.get_next_from_queue()

            if not project_data:
                # No projects in queue, wait and check again
                await asyncio.sleep(10)
                continue

            project_id = project_data["project_id"]
            repository.mark_queue_status(project_id, "analyzing")

            # Format budget string for analysis
            budget_min = project_data["budget_min"]
            budget_max = project_data["budget_max"]
            currency = project_data["currency"] or "USD"
            if budget_min and budget_max:
                budget_str = f"${budget_min:.0f} - ${budget_max:.0f} {currency}"
            elif budget_max:
                budget_str = f"Up to ${budget_max:.0f} {currency}"
            else:
                budget_str = "Not specified"

            logger.info(f"Analyzing project {project_id}: {project_data['title'][:50]}...")

            # Run Gemini analysis in thread pool (subprocess is blocking)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                analyze_project,
                project_id,
                project_data["title"],
                project_data["description"],
                budget_str,
            )

            if not result:
                logger.error(f"Failed to analyze project {project_id}")
                repository.mark_queue_status(project_id, "error")
                await asyncio.sleep(5)
                continue

            # Process the verdict
            if result.verdict == "SKIP":
                # Send SKIP notification
                try:
                    await notifier.send_skip_notification(
                        project_id=project_id,
                        title=project_data["title"],
                        budget_min=budget_min,
                        budget_max=budget_max,
                        currency=currency,
                        client_country=project_data["client_country"],
                        url=project_data["url"],
                        summary=result.summary,
                    )
                except Exception as e:
                    logger.error(f"Failed to send SKIP notification: {e}")

                repository.mark_queue_status(project_id, "processed")
                repository.add_processed_project(project_id)
                logger.info(f"Project {project_id} SKIPPED: {result.summary[:80]}...")

            else:
                # Verdict is BID
                bid_amount = result.amount or budget_max or 100

                # Store pending bid for when user clicks button
                repository.add_pending_bid(
                    project_id=project_id,
                    amount=bid_amount,
                    period=result.period,
                    description=result.bid_text,
                    title=project_data["title"],
                    currency=currency,
                )

                # Send BID notification to Telegram
                try:
                    sent = await notifier.send_gpt_decision_notification(
                        project_id=project_id,
                        title=project_data["title"],
                        budget_min=budget_min,
                        budget_max=budget_max,
                        currency=currency,
                        client_country=project_data["client_country"],
                        bid_count=project_data["bid_count"] or 0,
                        avg_bid=project_data["avg_bid"],
                        url=project_data["url"],
                        summary=result.summary,
                        bid_text=result.bid_text,
                        suggested_amount=bid_amount,
                        suggested_period=result.period,
                    )

                    if sent:
                        repository.mark_queue_status(project_id, "processed")
                        repository.add_processed_project(project_id)
                        logger.info(f"Project {project_id} BID sent to Telegram: ${bid_amount}")
                    else:
                        logger.error(f"Failed to send notification for {project_id}")

                except Exception as e:
                    logger.error(f"Error sending notification: {e}")

            # Small delay between analyses
            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Error in analysis loop: {e}")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - start/stop background tasks."""
    global _polling_task, _analysis_task

    logger.info("=" * 50)
    logger.info("Bid-Assist with Gemini Analysis starting...")
    logger.info("=" * 50)
    logger.info("Budget range and poll interval configurable via Telegram bot /settings")
    logger.info(f"Skills: {len(settings.skill_ids)} configured")
    logger.info(f"Max project age: {settings.max_project_age_hours}h")
    logger.info("AI: Gemini CLI (local)")

    # Start background tasks
    _polling_task = asyncio.create_task(polling_loop())
    _analysis_task = asyncio.create_task(analysis_loop())
    logger.info("Background polling task started")
    logger.info("Background analysis task started (Gemini CLI)")

    yield

    # Cleanup on shutdown
    for task in [_polling_task, _analysis_task]:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("Bid-Assist shutdown")


# Create FastAPI app
app = FastAPI(
    title="Bid-Assist API",
    description="REST API for Custom GPT integration with Freelancer bidding bot",
    version="2.0.0",
    lifespan=lifespan,
)

# Add CORS middleware (for local development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "ok",
        "service": "Bid-Assist API",
        "version": "2.0.0",
    }


@app.get("/health")
async def health():
    """Health check for monitoring."""
    return {"status": "healthy"}
