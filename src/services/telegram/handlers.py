"""Telegram command and callback handlers."""

import logging
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    Application,
)
from src.config import settings
from src.services.storage import ProjectRepository
from src.services.freelancer import FreelancerClient, BiddingService
from src.services.telegram.notifier import (
    escape_markdown_v2,
    get_pending_bid,
    remove_pending_bid,
)
from src.models import Bid

logger = logging.getLogger(__name__)

# Runtime state (shared across handlers)
_runtime_state = {
    "paused": False,
    "min_budget": settings.min_budget,
    "max_budget": settings.max_budget,
}

# Singleton bidding service
_bidding_service = None


def get_bidding_service() -> BiddingService:
    """Get or create bidding service."""
    global _bidding_service
    if _bidding_service is None:
        client = FreelancerClient()
        _bidding_service = BiddingService(client)
    return _bidding_service


def get_runtime_state() -> dict:
    """Get the current runtime state."""
    return _runtime_state


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Welcome to Bid-Assist!\n\n"
        "I monitor Freelancer for new projects matching your skills.\n\n"
        "When I find a good project, I'll send you:\n"
        "• AI-powered summary\n"
        "• Suggested bid amount\n"
        "• Ready-to-use bid proposal\n"
        "• A 'Place Bid' button\n\n"
        "Commands:\n"
        "/status - Show current status\n"
        "/setbudget <min> <max> - Set budget range\n"
        "/pause - Pause monitoring\n"
        "/resume - Resume monitoring\n"
        "/stats - Show bid statistics"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    state = get_runtime_state()
    repo = ProjectRepository()

    processed_count = repo.get_processed_count()
    bid_stats = repo.get_bid_stats()

    monitoring_status = "⏸️ PAUSED" if state["paused"] else "▶️ RUNNING"

    message = (
        f"📊 *Bid\\-Assist Status*\n\n"
        f"*Monitoring:* {monitoring_status}\n"
        f"*Budget range:* ${state['min_budget']} \\- ${state['max_budget']}\n"
        f"*Poll interval:* {settings.poll_interval}s\n\n"
        f"*Statistics:*\n"
        f"• Projects processed: {processed_count}\n"
        f"• Total bids placed: {bid_stats['total_bids']}\n"
        f"• Successful bids: {bid_stats['successful_bids']}\n"
        f"• Avg bid amount: ${bid_stats['avg_amount']}"
    )

    await update.message.reply_text(message, parse_mode="MarkdownV2")


async def cmd_setbudget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setbudget command."""
    args = context.args

    if len(args) != 2:
        await update.message.reply_text(
            f"Current budget range: ${_runtime_state['min_budget']} - ${_runtime_state['max_budget']}\n\n"
            "Usage: /setbudget <min> <max>\n"
            "Example: /setbudget 20 300"
        )
        return

    try:
        min_budget = int(args[0])
        max_budget = int(args[1])

        if min_budget < 0 or max_budget < 0:
            raise ValueError("Budgets must be positive")
        if min_budget >= max_budget:
            raise ValueError("Min must be less than max")

        _runtime_state["min_budget"] = min_budget
        _runtime_state["max_budget"] = max_budget

        await update.message.reply_text(
            f"✅ Budget range updated: ${min_budget} - ${max_budget}"
        )
        logger.info(f"Budget range updated: ${min_budget} - ${max_budget}")

    except ValueError as e:
        await update.message.reply_text(
            f"❌ Invalid input: {e}\n"
            "Usage: /setbudget <min> <max>"
        )


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /pause command."""
    if _runtime_state["paused"]:
        await update.message.reply_text("⏸️ Monitoring is already paused.")
        return

    _runtime_state["paused"] = True
    await update.message.reply_text(
        "⏸️ Monitoring PAUSED.\n"
        "Use /resume to continue."
    )
    logger.info("Monitoring paused via Telegram command")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /resume command."""
    if not _runtime_state["paused"]:
        await update.message.reply_text("▶️ Monitoring is already running.")
        return

    _runtime_state["paused"] = False
    await update.message.reply_text("▶️ Monitoring RESUMED!")
    logger.info("Monitoring resumed via Telegram command")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command."""
    repo = ProjectRepository()
    stats = repo.get_bid_stats()

    success_rate = 0
    if stats["total_bids"] > 0:
        success_rate = (stats["successful_bids"] / stats["total_bids"]) * 100

    message = (
        f"📈 *Bid Statistics*\n\n"
        f"Total bids: {stats['total_bids']}\n"
        f"Successful: {stats['successful_bids']}\n"
        f"Success rate: {success_rate:.1f}%\n"
        f"Average amount: ${stats['avg_amount']}"
    )

    await update.message.reply_text(message, parse_mode="MarkdownV2")


async def handle_bid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Place Bid' button click."""
    query = update.callback_query
    await query.answer()  # Acknowledge the button click

    # Parse callback data: "bid:{project_id}"
    data = query.data
    if not data.startswith("bid:"):
        return

    try:
        project_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid bid data")
        return

    # Get pending bid data
    bid_data = get_pending_bid(project_id)
    if not bid_data:
        await query.edit_message_text(
            "❌ Bid data expired. Please wait for the next notification."
        )
        return

    # Show "placing bid..." status
    await query.edit_message_text("⏳ Placing bid...")

    # Place the bid
    bidding_service = get_bidding_service()
    repo = ProjectRepository()

    bid = Bid(
        project_id=project_id,
        amount=bid_data["amount"],
        period=bid_data["period"],
        milestone_percentage=settings.default_milestone_pct,
        description=bid_data["description"],
    )

    result = bidding_service.place_bid(bid)

    # Record in database
    repo.add_bid_record(
        project_id=project_id,
        amount=bid_data["amount"],
        period=bid_data["period"],
        description=bid_data["description"],
        success=result.success,
        error_message=result.message if not result.success else None,
    )

    # Remove from pending
    remove_pending_bid(project_id)

    # Update message with result
    if result.success:
        await query.edit_message_text(
            f"✅ Bid placed successfully!\n\n"
            f"Project: {bid_data['title']}\n"
            f"Amount: ${bid_data['amount']}\n"
            f"Period: {bid_data['period']} days"
        )
        logger.info(f"Bid placed on project {project_id}: ${bid_data['amount']}")
    else:
        await query.edit_message_text(
            f"❌ Bid failed\n\n"
            f"Error: {result.message}"
        )
        logger.error(f"Bid failed on project {project_id}: {result.message}")


def setup_handlers(application: Application):
    """Register all handlers with the application."""
    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("setbudget", cmd_setbudget))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("stats", cmd_stats))

    # Callback handler for Bid button
    application.add_handler(CallbackQueryHandler(handle_bid_callback, pattern="^bid:"))

    logger.info("Telegram handlers registered")
