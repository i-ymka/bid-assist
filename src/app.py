"""Bid-Assist: Automated Freelancer Project Discovery and Bidding.

This is the main application entry point that orchestrates:
- Periodic polling for new projects
- Filtering based on skills, budget, and blacklist
- AI-powered analysis and bid proposal generation
- Optional automatic bid placement
- Telegram notifications
"""

import logging
import sys
from telegram.ext import ContextTypes

from src.config import settings
from src.services.freelancer import FreelancerClient, ProjectService, BiddingService
from src.services.ai import AIAnalyzer
from src.services.telegram import TelegramBot, Notifier
from src.services.telegram.handlers import get_runtime_state
from src.services.storage import ProjectRepository
from src.filters import FilterPipeline, BudgetFilter
from src.models import Bid

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# Initialize services (singleton pattern)
_repository = None
_project_service = None
_bidding_service = None
_ai_analyzer = None
_notifier = None


def get_repository() -> ProjectRepository:
    global _repository
    if _repository is None:
        _repository = ProjectRepository()
    return _repository


def get_project_service() -> ProjectService:
    global _project_service
    if _project_service is None:
        client = FreelancerClient()
        _project_service = ProjectService(client)
    return _project_service


def get_bidding_service() -> BiddingService:
    global _bidding_service
    if _bidding_service is None:
        client = FreelancerClient()
        _bidding_service = BiddingService(client)
    return _bidding_service


def get_ai_analyzer() -> AIAnalyzer:
    global _ai_analyzer
    if _ai_analyzer is None:
        _ai_analyzer = AIAnalyzer()
    return _ai_analyzer


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier


async def polling_cycle(context: ContextTypes.DEFAULT_TYPE):
    """Main polling cycle that runs periodically.

    This function:
    1. Checks if monitoring is paused
    2. Fetches new projects from Freelancer
    3. Filters unprocessed projects
    4. Gets full project details
    5. Applies filters (skills, budget, blacklist)
    6. Runs AI analysis on matching projects
    7. Optionally places automatic bids
    8. Sends Telegram notifications
    """
    runtime_state = get_runtime_state()

    # Check if paused
    if runtime_state.get("paused", False):
        logger.debug("Monitoring is paused, skipping cycle")
        return

    logger.info("--- Starting new polling cycle ---")

    repository = get_repository()
    project_service = get_project_service()
    bidding_service = get_bidding_service()
    ai_analyzer = get_ai_analyzer()
    notifier = get_notifier()

    # Step 1: Fetch project list
    projects = project_service.get_active_projects()
    if not projects:
        logger.info("No projects found in this cycle")
        return

    # Step 2: Filter already processed
    unprocessed = [p for p in projects if not repository.is_processed(p.id)]
    if not unprocessed:
        logger.info("All fetched projects were already processed")
        return

    logger.info(f"Found {len(unprocessed)} unprocessed projects")

    # Step 3: Create filter pipeline with runtime budget settings
    filter_pipeline = FilterPipeline([
        # SkillFilter is included by default
        # BlacklistFilter is included by default
        BudgetFilter(
            min_budget=runtime_state.get("min_budget", settings.min_budget),
            max_budget=runtime_state.get("max_budget", settings.max_budget),
        ),
    ])

    # Step 4: Process each project
    for project_preview in unprocessed:
        project_id = project_preview.id

        # Get full project details
        project = project_service.get_project_details(project_id)
        if not project:
            repository.add_processed_project(project_id)
            continue

        # Apply filters
        passed, reason = filter_pipeline.evaluate(project)
        if not passed:
            logger.info(f"Project {project_id} filtered: {reason}")
            repository.add_processed_project(project_id)
            continue

        logger.info(f"Project {project_id} passed filters, running AI analysis...")

        # AI Analysis
        analysis = ai_analyzer.analyze_project(project)

        # Auto-bid if enabled
        bid_result = None
        if runtime_state.get("auto_bid_enabled", False):
            logger.info(f"Auto-bid enabled, placing bid on project {project_id}")

            # Determine bid amount
            bid_amount = analysis.suggested_amount or project.budget.maximum
            bid_period = analysis.suggested_period or settings.default_bid_period

            bid = Bid(
                project_id=project_id,
                amount=bid_amount,
                period=bid_period,
                milestone_percentage=settings.default_milestone_pct,
                description=analysis.suggested_bid_text,
            )

            bid_result = bidding_service.place_bid(bid)

            # Record bid in history
            repository.add_bid_record(
                project_id=project_id,
                amount=bid_amount,
                period=bid_period,
                description=analysis.suggested_bid_text,
                success=bid_result.success,
                error_message=bid_result.message if not bid_result.success else None,
            )

        # Send notification
        await notifier.send_project_notification(project, analysis, bid_result)

        # Mark as processed
        repository.add_processed_project(project_id)

    logger.info("--- Polling cycle complete ---")


def main():
    """Main entry point for the application."""
    logger.info("=" * 50)
    logger.info("Bid-Assist starting...")
    logger.info("=" * 50)

    # Validate configuration
    if not settings.telegram_bot_token:
        logger.critical("TELEGRAM_BOT_TOKEN not configured!")
        sys.exit(1)

    if not settings.freelancer_oauth_token:
        logger.critical("FREELANCER_OAUTH_TOKEN not configured!")
        sys.exit(1)

    # Log configuration
    logger.info(f"Poll interval: {settings.poll_interval}s")
    logger.info(f"Budget range: ${settings.min_budget} - ${settings.max_budget}")
    logger.info(f"Skills: {len(settings.skill_ids)} configured")
    logger.info(f"Auto-bid: {'ENABLED' if settings.auto_bid_enabled else 'DISABLED'}")
    logger.info(f"AI model: {settings.llm_model}")

    # Build and start bot
    bot = TelegramBot()
    application = bot.build()

    # Schedule polling job
    job_queue = application.job_queue
    job_queue.run_repeating(
        polling_cycle,
        interval=settings.poll_interval,
        first=5,  # Start first poll 5 seconds after bot starts
    )

    logger.info(f"First poll will start in 5 seconds...")
    logger.info("Bot is running. Use Telegram commands to control.")

    # Start the bot
    application.run_polling()


if __name__ == "__main__":
    main()
