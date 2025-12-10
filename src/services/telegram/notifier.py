"""Telegram notification service."""

import logging
from typing import Optional
from telegram import Bot
from src.models import Project, AIAnalysis, BidResult
from src.config import settings

logger = logging.getLogger(__name__)


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2.

    Args:
        text: Text to escape.

    Returns:
        Escaped text safe for MarkdownV2.
    """
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    safe_text = str(text) if text else ""
    for char in escape_chars:
        safe_text = safe_text.replace(char, f"\\{char}")
    return safe_text


class Notifier:
    """Service for sending Telegram notifications."""

    def __init__(self, bot_token: str = None, chat_ids: list = None):
        """Initialize the notifier.

        Args:
            bot_token: Telegram bot token. If None, uses settings.telegram_bot_token.
            chat_ids: List of chat IDs to send to. If None, uses settings.telegram_chat_ids.
        """
        self._token = bot_token or settings.telegram_bot_token
        self._chat_ids = chat_ids or settings.telegram_chat_ids
        self._bot = Bot(token=self._token)

    async def send_project_notification(
        self,
        project: Project,
        analysis: AIAnalysis,
        bid_result: Optional[BidResult] = None,
    ) -> bool:
        """Send a notification about a project.

        Args:
            project: The project to notify about.
            analysis: AI analysis of the project.
            bid_result: Optional result of automatic bid placement.

        Returns:
            True if notification sent successfully to at least one chat.
        """
        text = self._format_project_message(project, analysis, bid_result)
        return await self._send_to_all_chats(text)

    def _format_project_message(
        self,
        project: Project,
        analysis: AIAnalysis,
        bid_result: Optional[BidResult] = None,
    ) -> str:
        """Format a project notification message."""
        title = escape_markdown_v2(project.title)
        summary = escape_markdown_v2(analysis.summary)
        bid_text = escape_markdown_v2(analysis.suggested_bid_text)
        budget_str = escape_markdown_v2(project.budget_str)
        project_url = escape_markdown_v2(project.url)
        hashtag = f"\\#{analysis.difficulty.value}"

        # Build message
        lines = [
            f"*{title}*\n",
            f"📝 *Summary:* {summary}\n",
            f"💰 *Budget:* {budget_str}\n",
        ]

        # Add suggested bid amount if available
        if analysis.suggested_amount:
            suggested = escape_markdown_v2(f"${analysis.suggested_amount}")
            lines.append(f"💵 *Suggested Bid:* {suggested}\n")

        lines.append(f"\n🔗 *Project link:*\n{project_url}\n")
        lines.append(f"\n👇 *Bid Proposal:*\n```\n{bid_text}\n```\n")

        # Add auto-bid result if available
        if bid_result:
            if bid_result.success:
                lines.append(f"\n✅ *Auto\\-bid placed successfully\\!*\n")
            else:
                error = escape_markdown_v2(bid_result.message)
                lines.append(f"\n❌ *Auto\\-bid failed:* {error}\n")

        lines.append(f"\n{hashtag}")

        return "".join(lines)

    async def send_status_message(self, status_text: str) -> bool:
        """Send a status/info message.

        Args:
            status_text: The status message to send (plain text, will be escaped).

        Returns:
            True if sent successfully.
        """
        escaped = escape_markdown_v2(status_text)
        return await self._send_to_all_chats(escaped)

    async def send_raw_message(self, text: str, parse_mode: str = "MarkdownV2") -> bool:
        """Send a raw message (no escaping).

        Args:
            text: Pre-formatted message text.
            parse_mode: Telegram parse mode.

        Returns:
            True if sent successfully.
        """
        return await self._send_to_all_chats(text, parse_mode=parse_mode)

    async def _send_to_all_chats(
        self,
        text: str,
        parse_mode: str = "MarkdownV2",
    ) -> bool:
        """Send a message to all configured chat IDs.

        Returns:
            True if sent to at least one chat successfully.
        """
        if not self._chat_ids:
            logger.warning("No chat IDs configured for notifications")
            return False

        success = False
        for chat_id in self._chat_ids:
            try:
                await self._bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
                logger.debug(f"Notification sent to chat {chat_id}")
                success = True
            except Exception as e:
                logger.error(f"Failed to send notification to chat {chat_id}: {e}")

        return success
