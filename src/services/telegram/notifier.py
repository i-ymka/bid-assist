"""Telegram notification service with Bid button."""

import logging
from typing import Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from src.models import Project, AIAnalysis
from src.config import settings

logger = logging.getLogger(__name__)

# Cache for pending bids (project_id -> bid_data)
# This stores bid info until user clicks the button
_pending_bids = {}


def get_pending_bid(project_id: int) -> Optional[dict]:
    """Get pending bid data for a project."""
    return _pending_bids.get(project_id)


def remove_pending_bid(project_id: int):
    """Remove pending bid data after it's been used."""
    _pending_bids.pop(project_id, None)


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    safe_text = str(text) if text else ""
    for char in escape_chars:
        safe_text = safe_text.replace(char, f"\\{char}")
    return safe_text


class Notifier:
    """Service for sending Telegram notifications with Bid button."""

    def __init__(self, bot_token: str = None, chat_ids: list = None):
        """Initialize the notifier."""
        self._token = bot_token or settings.telegram_bot_token
        self._chat_ids = chat_ids or settings.telegram_chat_ids
        self._bot = Bot(token=self._token)

    async def send_project_notification(
        self,
        project: Project,
        analysis: AIAnalysis,
    ) -> bool:
        """Send a notification about a project with a Bid button.

        Args:
            project: The project to notify about.
            analysis: AI analysis of the project.

        Returns:
            True if notification sent successfully to at least one chat.
        """
        # Store bid data for when user clicks button
        bid_amount = analysis.suggested_amount or project.budget.maximum
        bid_period = analysis.suggested_period or settings.default_bid_period

        _pending_bids[project.id] = {
            "project_id": project.id,
            "amount": bid_amount,
            "period": bid_period,
            "description": analysis.suggested_bid_text,
            "title": project.title,
        }

        text = self._format_project_message(project, analysis)
        keyboard = self._create_bid_keyboard(project.id, bid_amount)

        return await self._send_to_all_chats(text, keyboard)

    def _format_project_message(
        self,
        project: Project,
        analysis: AIAnalysis,
    ) -> str:
        """Format a project notification message."""
        title = escape_markdown_v2(project.title)
        summary = escape_markdown_v2(analysis.summary)
        bid_text = escape_markdown_v2(analysis.suggested_bid_text)
        budget_str = escape_markdown_v2(project.budget_str)
        project_url = escape_markdown_v2(project.url)
        hashtag = f"\\#{analysis.difficulty.value}"

        # Client info
        country = escape_markdown_v2(project.owner.country)
        bid_count = project.bid_stats.bid_count
        avg_bid = project.avg_bid_str

        lines = [
            f"*{title}*\n",
            f"\n📝 *Summary:* {summary}\n",
            f"\n📊 *Project Info:*\n",
            f"  💰 Budget: {budget_str}\n",
            f"  🏷️ Bids: {bid_count} \\(avg: {escape_markdown_v2(avg_bid)}\\)\n",
            f"  🌍 Client: {country}\n",
        ]

        # Add NDA warning if required
        if project.nda_required:
            lines.append(f"  ⚠️ *NDA Required*\n")

        # Add AI suggestions
        lines.append(f"\n💡 *AI Suggestion:*\n")
        if analysis.suggested_amount:
            suggested = escape_markdown_v2(f"${analysis.suggested_amount:.0f}")
            lines.append(f"  💵 Bid: {suggested}")
            if analysis.suggested_period:
                lines.append(f" for {analysis.suggested_period} days")
            lines.append("\n")

        lines.append(f"\n🔗 *Link:* {project_url}\n")
        lines.append(f"\n👇 *Bid Proposal:*\n```\n{bid_text}\n```\n")
        lines.append(f"\n{hashtag}")

        return "".join(lines)

    def _create_bid_keyboard(
        self,
        project_id: int,
        amount: float,
    ) -> InlineKeyboardMarkup:
        """Create inline keyboard with Bid button."""
        button_text = f"💰 Place Bid (${amount:.0f})"
        callback_data = f"bid:{project_id}"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(button_text, callback_data=callback_data)]
        ])
        return keyboard

    async def send_status_message(self, status_text: str) -> bool:
        """Send a status/info message."""
        escaped = escape_markdown_v2(status_text)
        return await self._send_to_all_chats(escaped)

    async def _send_to_all_chats(
        self,
        text: str,
        keyboard: InlineKeyboardMarkup = None,
        parse_mode: str = "MarkdownV2",
    ) -> bool:
        """Send a message to all configured chat IDs."""
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
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                logger.debug(f"Notification sent to chat {chat_id}")
                success = True
            except Exception as e:
                logger.error(f"Failed to send notification to chat {chat_id}: {e}")

        return success
