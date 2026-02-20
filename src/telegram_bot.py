"""Standalone Telegram bot for handling bid confirmations.

This runs separately from the FastAPI server.
Only handles Telegram callbacks (Place Bid, Edit Amount, etc.)
No AI analysis - that's done by Custom GPT.
"""

import asyncio
import logging
from telegram.ext import Application

from src.config import settings
from src.services.telegram.handlers import setup_handlers

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def main():
    """Run the Telegram bot."""
    logger.info("=" * 50)
    logger.info("Telegram Bot starting (Custom GPT mode)...")
    logger.info("=" * 50)
    logger.info("This bot only handles bid confirmations.")
    logger.info("AI analysis is done by Custom GPT.")

    # Build Telegram application
    application = Application.builder().token(settings.telegram_bot_token).build()

    # Register handlers
    setup_handlers(application)

    logger.info("Telegram bot is running. Waiting for button clicks...")

    # Run the bot
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
