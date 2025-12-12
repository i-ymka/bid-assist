"""Bid-Assist: Automated Freelancer Project Discovery and Bidding.

This is the main application entry point that orchestrates:
- Periodic polling for new projects
- Filtering based on skills, budget, and blacklist
- AI-powered analysis and bid proposal generation
- Telegram notifications with "Place Bid" button
"""

import logging
import sys
from telegram.ext import ContextTypes

from src.config import settings
from src.services.freelancer import FreelancerClient, ProjectService
from src.services.ai import AIAnalyzer
from src.services.telegram import TelegramBot, Notifier
from src.services.telegram.handlers import get_runtime_state
from src.services.storage import ProjectRepository
from src.filters import FilterPipeline, BudgetFilter

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
    7. Sends Telegram notification with "Place Bid" button
    """
    runtime_state = get_runtime_state()

    # Check if paused
    if runtime_state.get("paused", False):
        logger.debug("Monitoring is paused, skipping cycle")
        return

    logger.info("--- Starting new polling cycle ---")

    repository = get_repository()
    project_service = get_project_service()
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

        # Only send notification if AI recommends bidding
        if analysis.should_bid:
            await notifier.send_project_notification(project, analysis)
            logger.info(f"Project {project_id}: AI recommends BID, notification sent")
        else:
            logger.info(f"Project {project_id}: AI recommends SKIP - {analysis.summary[:100]}")

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

    logger.info("First poll will start in 5 seconds...")
    logger.info("Bot is running. Click 'Place Bid' button to bid on projects.")

    # Start the bot
    application.run_polling()


if __name__ == "__main__":
    main()
