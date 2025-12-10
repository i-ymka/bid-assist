"""Telegram bot setup and management."""

import logging
from telegram.ext import Application, ApplicationBuilder
from src.config import settings
from src.services.telegram.handlers import setup_handlers

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot wrapper for Bid-Assist."""

    def __init__(self, token: str = None):
        """Initialize the Telegram bot.

        Args:
            token: Bot token. If None, uses settings.telegram_bot_token.
        """
        self._token = token or settings.telegram_bot_token
        self._application: Application = None

    def build(self) -> Application:
        """Build and configure the Telegram application.

        Returns:
            Configured Application instance.
        """
        if not self._token:
            raise ValueError("Telegram bot token not configured")

        logger.info("Building Telegram application...")

        self._application = (
            ApplicationBuilder()
            .token(self._token)
            .build()
        )

        # Register command handlers
        setup_handlers(self._application)

        return self._application

    @property
    def application(self) -> Application:
        """Get the Telegram Application instance."""
        if self._application is None:
            self.build()
        return self._application

    @property
    def job_queue(self):
        """Get the job queue for scheduling tasks."""
        return self.application.job_queue

    def run(self):
        """Start the bot with polling."""
        logger.info("Starting Telegram bot...")
        self.application.run_polling()
