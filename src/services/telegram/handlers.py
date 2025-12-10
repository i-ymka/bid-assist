"""Telegram command handlers for bot control."""

import logging
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    Application,
)
from src.config import settings
from src.services.storage import ProjectRepository
from src.services.telegram.notifier import escape_markdown_v2

logger = logging.getLogger(__name__)

# Runtime state (shared across handlers)
_runtime_state = {
    "auto_bid_enabled": settings.auto_bid_enabled,
    "paused": False,
    "min_budget": settings.min_budget,
    "max_budget": settings.max_budget,
}


def get_runtime_state() -> dict:
    """Get the current runtime state."""
    return _runtime_state


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Welcome to Bid-Assist!\n\n"
        "I monitor Freelancer for new projects matching your skills and send you notifications with AI-generated bid proposals.\n\n"
        "Commands:\n"
        "/status - Show current status\n"
        "/autobid on|off - Toggle automatic bidding\n"
        "/setbudget <min> <max> - Set budget range\n"
        "/pause - Pause project monitoring\n"
        "/resume - Resume monitoring\n"
        "/stats - Show bid statistics"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    state = get_runtime_state()
    repo = ProjectRepository()

    processed_count = repo.get_processed_count()
    bid_stats = repo.get_bid_stats()

    auto_bid_status = "🟢 ON" if state["auto_bid_enabled"] else "🔴 OFF"
    monitoring_status = "⏸️ PAUSED" if state["paused"] else "▶️ RUNNING"

    message = (
        f"📊 *Bid\\-Assist Status*\n\n"
        f"*Monitoring:* {monitoring_status}\n"
        f"*Auto\\-bid:* {auto_bid_status}\n"
        f"*Budget range:* ${state['min_budget']} \\- ${state['max_budget']}\n"
        f"*Poll interval:* {settings.poll_interval}s\n\n"
        f"*Statistics:*\n"
        f"• Projects processed: {processed_count}\n"
        f"• Total bids placed: {bid_stats['total_bids']}\n"
        f"• Successful bids: {bid_stats['successful_bids']}\n"
        f"• Avg bid amount: ${bid_stats['avg_amount']}"
    )

    await update.message.reply_text(message, parse_mode="MarkdownV2")


async def cmd_autobid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /autobid command."""
    args = context.args

    if not args:
        current = "enabled" if _runtime_state["auto_bid_enabled"] else "disabled"
        await update.message.reply_text(
            f"Auto-bid is currently {current}.\n"
            "Use /autobid on or /autobid off to change."
        )
        return

    action = args[0].lower()

    if action == "on":
        _runtime_state["auto_bid_enabled"] = True
        await update.message.reply_text(
            "✅ Auto-bid ENABLED!\n"
            "I will now automatically place bids on matching projects."
        )
        logger.info("Auto-bid enabled via Telegram command")

    elif action == "off":
        _runtime_state["auto_bid_enabled"] = False
        await update.message.reply_text(
            "🔴 Auto-bid DISABLED.\n"
            "I will only send notifications without placing bids."
        )
        logger.info("Auto-bid disabled via Telegram command")

    else:
        await update.message.reply_text(
            "Usage: /autobid on or /autobid off"
        )


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


def setup_handlers(application: Application):
    """Register all command handlers with the application.

    Args:
        application: The Telegram Application instance.
    """
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("autobid", cmd_autobid))
    application.add_handler(CommandHandler("setbudget", cmd_setbudget))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("stats", cmd_stats))

    logger.info("Telegram command handlers registered")
