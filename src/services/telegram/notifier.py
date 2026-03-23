"""Telegram notification service with Bid button."""

import asyncio
import logging
import random
from typing import Optional
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Message
from src.models import Project, AIAnalysis
from src.config import settings
from src.services.storage import ProjectRepository
from src.services.freelancer.bidding import strip_markdown
from src.services.currency import to_usd, from_usd, round_up_10

logger = logging.getLogger(__name__)

# Custom emoji: (id, unicode_fallback) — Telegram Premium
_CE = {
    "summary": ("5188156318443141940", "📝"),
    "stats":   ("5334544901428229844", "📊"),
    "budget":  ("5240290577102152084", "💰"),
    "bids":    ("5237931961451815445", "🏷"),
    "country": ("5348474966427841129", "🌍"),
    "check":   ("5364035134725043602", "✅"),
    "link":    ("5974492756494519709", "🔗"),
    "proposal":("5192825506239616944", "👇"),
    "ticket":  ("5238048140317170954", "🎟"),
}

_HEADER_EMOJIS = [
    ("5188440323155588640", "🤖"), ("5188361166908322403", "⚡"),
    ("5188164934147535346", "🚀"), ("5188587713548284884", "🎯"),
    ("5224649148023710232", "💎"), ("5449681528546152415", "🔥"),
    ("5370757703436082874", "✨"), ("5211042120399347150", "💫"),
    ("5240076365608264313", "🌟"),
]


def ce(name: str) -> str:
    """Custom emoji in MarkdownV2 format."""
    entry = _CE.get(name)
    if not entry:
        return ""
    eid, fallback = entry
    return f"![{fallback}](tg://emoji?id={eid})"


def random_header_emoji() -> str:
    """Random header custom emoji in MarkdownV2 format."""
    eid, fallback = random.choice(_HEADER_EMOJIS)
    return f"![{fallback}](tg://emoji?id={eid})"


def get_pending_bid(project_id: int) -> Optional[dict]:
    """Get pending bid data for a project from the repository."""
    repo = ProjectRepository()
    return repo.get_pending_bid(project_id)


def remove_pending_bid(project_id: int):
    """Remove pending bid data after it's been used."""
    repo = ProjectRepository()
    repo.remove_pending_bid(project_id)


def update_pending_bid(project_id: int, amount: float = None, description: str = None):
    """Update pending bid data (amount or description).

    Returns:
        Updated bid data dict or None if not found.
    """
    repo = ProjectRepository()
    return repo.update_pending_bid(project_id, amount=amount, description=description)


def create_updated_keyboard(project_id: int, amount: float, currency: str = None) -> InlineKeyboardMarkup:
    """Create keyboard with updated amount for edited bids."""
    # Get currency from database if not provided
    if currency is None:
        from src.services.storage import ProjectRepository
        repo = ProjectRepository()
        bid_data = repo.get_pending_bid(project_id)
        currency = bid_data.get("currency", "USD") if bid_data else "USD"

    edit_amount_btn = InlineKeyboardButton(
        "✏️ Edit Amount",
        callback_data=f"edit_amount:{project_id}",
        api_kwargs={"style": "primary"},
    )
    edit_text_btn = InlineKeyboardButton(
        "✏️ Edit Proposal",
        callback_data=f"edit_text:{project_id}",
        api_kwargs={"style": "primary"},
    )
    bid_btn = InlineKeyboardButton(
        f"💰 Place Bid ({amount:.0f} {currency})",
        callback_data=f"bid:{project_id}",
        api_kwargs={"style": "success"},
    )
    return InlineKeyboardMarkup([
        [edit_amount_btn, edit_text_btn],
        [bid_btn]
    ])


def rebuild_bid_message(bid_data: dict) -> str:
    """Rebuild the full bid notification message from bid_data.

    Used when updating the original message after edits (amount/proposal).
    Matches variant 1 structure.
    """
    title = escape_markdown_v2(bid_data.get("title", "Unknown Project"))
    bid_text_clean = strip_markdown(bid_data.get("description", ""))
    bid_text = escape_markdown_v2(bid_text_clean)
    currency = bid_data.get("currency", "USD")
    amount = bid_data.get("amount", 0)
    period = bid_data.get("period", 3)
    url = bid_data.get("url", "")
    bid_count = bid_data.get("bid_count", 0)
    summary = bid_data.get("summary", "")
    budget_min = bid_data.get("budget_min", 0)
    budget_max = bid_data.get("budget_max", 0)
    client_country = bid_data.get("client_country", "")
    avg_bid = bid_data.get("avg_bid", 0)

    if budget_min and budget_max:
        budget_str = escape_markdown_v2(f"{budget_min:.0f} - {budget_max:.0f} {currency}")
    elif budget_max:
        budget_str = escape_markdown_v2(f"up to {budget_max:.0f} {currency}")
    else:
        budget_str = "Not specified"

    avg_bid_str = escape_markdown_v2(f"{avg_bid:.0f} {currency}") if avg_bid else "N/A"
    amount_str = escape_markdown_v2(f"{amount:.0f} {currency}")
    link = _link_url(url)

    lines = [
        f"*{title}*\n",
        f"\n{ce('summary')} Summary: {escape_markdown_v2(summary)}\n" if summary else "",
        f"\n{ce('budget')} Budget: {budget_str}\n",
        f"{ce('bids')} Bids: {bid_count} \\(avg: {avg_bid_str}\\)\n",
        f"{ce('country')} Client: {escape_markdown_v2(client_country or 'Unknown')}\n",
        f"{ce('link')} [Open project]({link})\n" if url else "",
        f"\n{'─' * 30}\n",
        f"\n💡 AI said: __Bid: {amount_str} for {period} days__\n",
        f"\n{ce('proposal')} Bid Proposal:\n```\n{bid_text}\n```\n",
        f"\n\\#BID",
    ]

    return "".join(lines)


def build_bid_placed_message(
    bid_data: dict,
    rank_info: dict = None,
    remaining_bids: int = None,
) -> str:
    """Build variant 2 message: manual bid placed.

    Header at top, bid result at bottom after proposal.
    """
    title = escape_markdown_v2(bid_data.get("title", "Unknown Project"))
    bid_text_clean = strip_markdown(bid_data.get("description", ""))
    bid_text = escape_markdown_v2(bid_text_clean)
    currency = bid_data.get("currency", "USD")
    amount = bid_data.get("amount", 0)
    period = bid_data.get("period", 3)
    url = bid_data.get("url", "")
    bid_count = bid_data.get("bid_count", 0)
    summary = bid_data.get("summary", "")
    budget_min = bid_data.get("budget_min", 0)
    budget_max = bid_data.get("budget_max", 0)
    client_country = bid_data.get("client_country", "")
    avg_bid = bid_data.get("avg_bid", 0)

    if budget_min and budget_max:
        budget_str = escape_markdown_v2(f"{budget_min:.0f} - {budget_max:.0f} {currency}")
    elif budget_max:
        budget_str = escape_markdown_v2(f"up to {budget_max:.0f} {currency}")
    else:
        budget_str = "Not specified"

    # Build bids line with rank if available
    if rank_info:
        rank = rank_info.get("rank")
        total = rank_info.get("total_bids", bid_count)
        avg = rank_info.get("avg_bid", avg_bid)
        avg_str = escape_markdown_v2(f"{avg:.0f} {currency}") if avg else "N/A"
        if rank:
            bids_line = f"{ce('bids')} My bid: \\#{rank} out of {total} \\(avg: {avg_str}\\)\n"
        else:
            bids_line = f"{ce('bids')} Bids: {total} \\(avg: {avg_str}\\)\n"
    else:
        avg_str = escape_markdown_v2(f"{avg_bid:.0f} {currency}") if avg_bid else "N/A"
        bids_line = f"{ce('bids')} Bids: {bid_count} \\(avg: {avg_str}\\)\n"

    amount_str = escape_markdown_v2(f"{amount:.0f} {currency}")
    link = _link_url(url)

    lines = [
        f"{random_header_emoji()} *BID PLACED\\!*\n",
        f"*{title}*\n",
        f"\n{ce('summary')} Summary: {escape_markdown_v2(summary)}\n" if summary else "",
        f"\n{ce('budget')} Budget: {budget_str}\n",
        bids_line,
        f"{ce('country')} Client: {escape_markdown_v2(client_country or 'Unknown')}\n",
        f"{ce('link')} [Open project]({link})\n" if url else "",
        f"\n{'─' * 30}\n",
        f"\n{ce('proposal')} Bid Proposal:\n```\n{bid_text}\n```\n",
        f"\n{ce('check')} {amount_str} · {period} days\n",
    ]

    if remaining_bids is not None:
        lines.append(f"{ce('ticket')} {remaining_bids} bids remaining\n")

    lines.append(f"\n\\#BID")

    return "".join(lines)


def escape_markdown_v2(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    safe_text = str(text) if text else ""
    for char in escape_chars:
        safe_text = safe_text.replace(char, f"\\{char}")
    return safe_text


def _link_url(url: str) -> str:
    """Escape URL for use inside MarkdownV2 inline link parens.

    Only ) and \\ need escaping inside (...) of [text](url).
    """
    return url.replace("\\", "\\\\").replace(")", "\\)")


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

        repo = ProjectRepository()
        repo.add_pending_bid(
            project_id=project.id,
            amount=bid_amount,
            period=bid_period,
            description=analysis.suggested_bid_text,
            title=project.title,
            currency=project.currency.code,
            url=project.url,
            bid_count=project.bid_stats.bid_count,
            summary=analysis.summary,
            budget_min=project.budget.minimum,
            budget_max=project.budget.maximum,
            client_country=project.owner.country,
            avg_bid=project.bid_stats.bid_avg,
        )

        text = self._format_project_message(project, analysis)
        keyboard = self._create_bid_keyboard(project.id, bid_amount, project.currency.code)

        return await self._send_to_all_chats(text, keyboard)

    def _format_project_message(
        self,
        project: Project,
        analysis: AIAnalysis,
    ) -> str:
        """Format a project notification message."""
        title = escape_markdown_v2(project.title)
        summary = escape_markdown_v2(analysis.summary)
        bid_text_clean = strip_markdown(analysis.suggested_bid_text)
        bid_text = escape_markdown_v2(bid_text_clean)
        budget_str = escape_markdown_v2(project.budget_str)
        hashtag = f"\\#{analysis.verdict.value}"

        country = escape_markdown_v2(project.owner.country)
        bid_count = project.bid_stats.bid_count
        avg_bid = project.avg_bid_str
        link_url = _link_url(project.url)

        lines = [
            f"*{title}*\n",
            f"\n{ce('summary')} Summary: {summary}\n",
            f"\n{ce('budget')} Budget: {budget_str}\n",
            f"{ce('bids')} Bids: {bid_count} \\(avg: {escape_markdown_v2(avg_bid)}\\)\n",
            f"{ce('country')} Client: {country}\n",
            f"{ce('link')} [Open project]({link_url})\n",
        ]

        if project.nda_required:
            lines.append(f"⚠️ *NDA Required*\n")

        lines.append(f"\n{'─' * 30}\n")

        if analysis.suggested_amount:
            currency_code = project.currency.code
            amount_str = escape_markdown_v2(f"{analysis.suggested_amount:.0f} {currency_code}")
            period_str = f" for {analysis.suggested_period} days" if analysis.suggested_period else ""
            lines.append(f"\n💡 AI said: __Bid: {amount_str}{period_str}__\n")

        lines.append(f"\n{ce('proposal')} Bid Proposal:\n```\n{bid_text}\n```\n")
        lines.append(f"\n{hashtag}")

        return "".join(lines)

    def _create_bid_keyboard(
        self,
        project_id: int,
        amount: float,
        currency: str = "USD",
    ) -> InlineKeyboardMarkup:
        """Create inline keyboard with Bid and Edit buttons."""
        edit_amount_btn = InlineKeyboardButton(
            "✏️ Edit Amount",
            callback_data=f"edit_amount:{project_id}",
            api_kwargs={"style": "primary"},
            )
        edit_text_btn = InlineKeyboardButton(
            "✏️ Edit Proposal",
            callback_data=f"edit_text:{project_id}",
            api_kwargs={"style": "primary"},
            )
        bid_btn = InlineKeyboardButton(
            f"💰 Place Bid ({amount:.0f} {currency})",
            callback_data=f"bid:{project_id}",
            api_kwargs={"style": "success"},
            )
        return InlineKeyboardMarkup([
            [edit_amount_btn, edit_text_btn],
            [bid_btn]
        ])

    async def send_status_message(self, status_text: str) -> bool:
        """Send a status/info message."""
        escaped = escape_markdown_v2(status_text)
        return await self._send_to_all_chats(escaped)

    async def send_gpt_decision_notification(
        self,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        bid_count: int,
        avg_bid: float,
        url: str,
        summary: str,
        bid_text: str,
        suggested_amount: float,
        suggested_period: int,
    ) -> bool:
        """Send notification based on GPT decision (for API integration).

        Args:
            project_id: Freelancer project ID
            title: Project title
            budget_min: Minimum budget
            budget_max: Maximum budget
            currency: Currency code
            client_country: Client's country
            bid_count: Number of bids
            avg_bid: Average bid amount
            url: Project URL
            summary: GPT's analysis summary
            bid_text: GPT's suggested bid proposal
            suggested_amount: GPT's suggested bid amount
            suggested_period: GPT's suggested delivery period

        Returns:
            True if notification sent successfully.
        """
        text, keyboard = self._format_bid_notification(
            project_id, title, budget_min, budget_max, currency,
            client_country, bid_count, avg_bid, url, summary,
            bid_text, suggested_amount, suggested_period
        )
        return await self._send_to_all_chats(text, keyboard)

    async def send_gpt_decision_notification_to_user(
        self,
        chat_id: str,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        bid_count: int,
        avg_bid: float,
        url: str,
        summary: str,
        bid_text: str,
        suggested_amount: float,
        suggested_period: int,
    ) -> Optional[Message]:
        """Send BID notification to a specific user.

        Returns:
            Message object if sent, None otherwise.
        """
        text, keyboard = self._format_bid_notification(
            project_id, title, budget_min, budget_max, currency,
            client_country, bid_count, avg_bid, url, summary,
            bid_text, suggested_amount, suggested_period
        )
        msg = await self.send_to_user(chat_id, text, keyboard)
        return msg, text, keyboard

    def _format_bid_notification(
        self,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        bid_count: int,
        avg_bid: float,
        url: str,
        summary: str,
        bid_text: str,
        suggested_amount: float,
        suggested_period: int,
    ) -> tuple:
        """Format BID notification message and keyboard.

        Returns:
            Tuple of (text, keyboard).
        """
        if budget_min and budget_max:
            budget_str = f"{budget_min:.0f} - {budget_max:.0f} {currency}"
        elif budget_max:
            budget_str = f"up to {budget_max:.0f} {currency}"
        else:
            budget_str = "Not specified"

        avg_bid_str = f"{avg_bid:.0f} {currency}" if avg_bid else "N/A"

        title_escaped = escape_markdown_v2(title)
        summary_escaped = escape_markdown_v2(summary)
        bid_text_clean = strip_markdown(bid_text)
        bid_text_escaped = escape_markdown_v2(bid_text_clean)
        budget_escaped = escape_markdown_v2(budget_str)
        country_escaped = escape_markdown_v2(client_country or "Unknown")
        avg_bid_escaped = escape_markdown_v2(avg_bid_str)
        amount_str = escape_markdown_v2(f"{suggested_amount:.0f} {currency}")
        link = _link_url(url)

        lines = [
            f"*{title_escaped}*\n",
            f"\n{ce('summary')} Summary: {summary_escaped}\n",
            f"\n{ce('budget')} Budget: {budget_escaped}\n",
            f"{ce('bids')} Bids: {bid_count} \\(avg: {avg_bid_escaped}\\)\n",
            f"{ce('country')} Client: {country_escaped}\n",
            f"{ce('link')} [Open project]({link})\n",
            f"\n{'─' * 30}\n",
            f"\n💡 AI said: __Bid: {amount_str} for {suggested_period} days__\n",
            f"\n{ce('proposal')} Bid Proposal:\n```\n{bid_text_escaped}\n```\n",
            f"\n\\#BID",
        ]

        text = "".join(lines)
        keyboard = self._create_bid_keyboard(project_id, suggested_amount, currency)

        return text, keyboard

    async def send_skip_notification(
        self,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        url: str,
        summary: str,
    ) -> bool:
        """Send notification for SKIP decision (silent, with Ask for Bid button).

        Args:
            project_id: Freelancer project ID
            title: Project title
            budget_min: Minimum budget
            budget_max: Maximum budget
            currency: Currency code
            client_country: Client's country
            url: Project URL
            summary: GPT's reason for skipping

        Returns:
            True if notification sent successfully.
        """
        text, keyboard = self._format_skip_notification(
            project_id, title, budget_min, budget_max, currency,
            client_country, url, summary
        )
        return await self._send_to_all_chats(text, keyboard=keyboard, silent=True)

    async def send_skip_notification_to_user(
        self,
        chat_id: str,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        url: str,
        summary: str,
    ) -> bool:
        """Send SKIP notification to a specific user.

        Args:
            chat_id: Telegram chat ID
            project_id: Freelancer project ID
            ... (same as send_skip_notification)

        Returns:
            True if notification sent successfully.
        """
        text, keyboard = self._format_skip_notification(
            project_id, title, budget_min, budget_max, currency,
            client_country, url, summary
        )
        return await self.send_to_user(chat_id, text, keyboard, silent=True)

    def _format_skip_notification(
        self,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        url: str,
        summary: str,
    ) -> tuple:
        """Format SKIP notification message and keyboard.

        Returns:
            Tuple of (text, keyboard).
        """
        # Format budget string
        if budget_min and budget_max:
            budget_str = f"{budget_min:.0f} - {budget_max:.0f} {currency}"
        elif budget_max:
            budget_str = f"up to {budget_max:.0f} {currency}"
        else:
            budget_str = "Not specified"

        # Build message
        title_escaped = escape_markdown_v2(title)
        summary_escaped = escape_markdown_v2(summary)
        budget_escaped = escape_markdown_v2(budget_str)
        url_escaped = escape_markdown_v2(url)
        country_escaped = escape_markdown_v2(client_country or "Unknown")

        lines = [
            f"*{title_escaped}*\n",
            f"\n❌ *SKIPPED*\n",
            f"\n{ce('summary')} *Reason:* {summary_escaped}\n",
            f"\n{ce('stats')} *Project Info:*\n",
            f"  {ce('budget')} Budget: {budget_escaped}\n",
            f"  {ce('country')} Client: {country_escaped}\n",
            f"\n{ce('link')} *Link:* {url_escaped}\n",
            f"\n\\#SKIP",
        ]

        text = "".join(lines)

        # Add "Ask for Bid" button so user can override
        ask_bid_btn = InlineKeyboardButton(
            "🔄 Ask for Bid Anyway",
            callback_data=f"ask_bid:{project_id}",
            api_kwargs={"style": "danger"},
        )
        keyboard = InlineKeyboardMarkup([[ask_bid_btn]])

        return text, keyboard

    async def _send_to_all_chats(
        self,
        text: str,
        keyboard: InlineKeyboardMarkup = None,
        parse_mode: str = "MarkdownV2",
        silent: bool = False,
    ) -> bool:
        """Send a message to all configured chat IDs.

        Args:
            text: Message text
            keyboard: Optional inline keyboard
            parse_mode: Parse mode for formatting
            silent: If True, send without notification sound
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
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                    disable_notification=silent,
                )
                logger.debug(f"Notification sent to chat {chat_id}")
                success = True
            except Exception as e:
                logger.error(f"Failed to send notification to chat {chat_id}: {e}")

        return success

    async def send_quota_exhausted_notification(self) -> None:
        """Notify user that all Gemini accounts/models have hit quota limits."""
        await self.send_to_all(
            "⚠️ <b>All Gemini accounts exhausted</b>\n\n"
            "All AI quota limits reached across all configured accounts.\n"
            "Bot will retry automatically. Add more free accounts to <code>GEMINI_HOME_POOL</code>.",
            parse_mode="HTML",
        )

    async def send_auto_bid_notification(
        self,
        chat_id: str,
        project_id: int,
        title: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        bid_count: int,
        avg_bid: float,
        url: str,
        summary: str,
        bid_text: str,
        amount: float,
        period: int,
        bid_id: int = None,
        rank_info: dict = None,
        remaining_bids: int = None,
        fair_price: Optional[float] = None,
    ) -> Optional[Message]:
        """Send auto-bid success notification. Returns Message for delayed updates."""
        if budget_min and budget_max:
            budget_str = f"{budget_min:.0f} - {budget_max:.0f} {currency}"
        elif budget_max:
            budget_str = f"up to {budget_max:.0f} {currency}"
        else:
            budget_str = "Not specified"

        title_escaped = escape_markdown_v2(title)
        summary_escaped = escape_markdown_v2(summary)
        bid_text_clean = strip_markdown(bid_text)
        bid_text_escaped = escape_markdown_v2(bid_text_clean)
        budget_escaped = escape_markdown_v2(budget_str)
        country_escaped = escape_markdown_v2(client_country or "Unknown")
        amount_escaped = escape_markdown_v2(f"{amount:.0f} {currency}")
        link = _link_url(url)

        # Build bids line with rank if available
        if rank_info:
            rank = rank_info.get("rank")
            total = rank_info.get("total_bids", bid_count)
            avg = rank_info.get("avg_bid", avg_bid)
            avg_str = escape_markdown_v2(f"{avg:.0f} {currency}") if avg else "N/A"
            if rank:
                bids_line = f"{ce('bids')} My bid: \\#{rank} out of {total} \\(avg: {avg_str}\\)\n"
            else:
                bids_line = f"{ce('bids')} Bids: {total} \\(avg: {avg_str}\\)\n"
        else:
            avg_str = escape_markdown_v2(f"{avg_bid:.0f} {currency}") if avg_bid else "N/A"
            bids_line = f"{ce('bids')} Bids: {bid_count} \\(avg: {avg_str}\\)\n"

        lines = [
            f"{random_header_emoji()} *AUTO\\-BID PLACED\\!*\n",
            f"*{title_escaped}*\n",
            f"\n{ce('summary')} Summary: {summary_escaped}\n",
            f"\n{ce('budget')} Budget: {budget_escaped}\n",
            bids_line,
            f"{ce('country')} Client: {country_escaped}\n",
            f"{ce('link')} [Open project]({link})\n" if url else "",
            f"\n{'─' * 30}\n",
            f"\n{ce('proposal')} Bid Proposal:\n```\n{bid_text_escaped}\n```\n",
            f"\n{ce('check')} {amount_escaped} · {period} days\n",
        ]

        if fair_price is not None:
            fair_price_escaped = escape_markdown_v2(f"~${fair_price:.0f}")
            lines.append(f"🤖 AI estimate: {fair_price_escaped}\n")
            amount_usd = to_usd(amount, currency)
            if fair_price < amount_usd * 0.5:
                our_usd_escaped = escape_markdown_v2(f"${amount_usd:.0f}")
                fp_escaped = escape_markdown_v2(f"${fair_price:.0f}")
                lines.append(f"⚠️ *AI PRICE WARNING: we bid {our_usd_escaped}, AI thinks {fp_escaped}*\n")

        if remaining_bids is not None:
            lines.append(f"{ce('ticket')} {remaining_bids} bids remaining\n")

        lines.append(f"\n\\#AUTOBID")

        text = "".join(lines)

        keyboard = None
        if url:
            check_url = f"{url}/proposals"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Check my bid", url=check_url, api_kwargs={"style": "primary"})],
            ])

        msg = await self.send_to_user(chat_id, text, keyboard)
        return msg, text, keyboard

    async def send_auto_bid_failed_notification(
        self,
        chat_id: str,
        project_id: int,
        title: str,
        url: str,
        amount: float,
        error: str,
    ) -> bool:
        """Send auto-bid failure notification."""
        title_escaped = escape_markdown_v2(title)
        error_escaped = escape_markdown_v2(error)
        amount_escaped = escape_markdown_v2(f"{amount:.0f}")

        lines = [
            f"❌ *AUTO\\-BID FAILED*\n",
            f"\n*{title_escaped}*\n",
            f"\n{ce('budget')} Amount: ${amount_escaped}\n",
            f"⚠️ Error: {error_escaped}\n",
        ]

        text = "".join(lines)

        keyboard = None
        if url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 View Project", url=url, api_kwargs={"style": "danger"})],
            ])

        return await self.send_to_user(chat_id, text, keyboard)

    async def send_to_user(
        self,
        chat_id: str,
        text: str,
        keyboard: InlineKeyboardMarkup = None,
        parse_mode: str = "MarkdownV2",
        silent: bool = False,
    ) -> Optional[Message]:
        """Send a message to a specific user.

        Returns:
            Message object if sent successfully, None otherwise.
            Truthy/falsy behavior preserved for backward compatibility.
        """
        try:
            msg = await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
                disable_web_page_preview=True,
                disable_notification=silent,
            )
            logger.debug(f"Notification sent to user {chat_id}")
            return msg
        except Exception as e:
            logger.error(f"Failed to send notification to user {chat_id}: {e}")
            return None


def replace_bids_line(text: str, rank=None, total=None, avg=None, currency="USD") -> str:
    """Replace the bids info line in message text with updated stats.

    Finds the line containing the bids emoji + "Bids:" or "My bid:" and
    replaces it with updated rank/total/avg info.

    Returns:
        Updated text, or original text if bids line not found.
    """
    bids_emoji = ce('bids')
    if not bids_emoji:
        return text

    # Build new content
    if rank and total:
        new_content = f"My bid: \\#{rank} out of {total}"
    elif total:
        new_content = f"Bids: {total}"
    else:
        return text

    if avg:
        avg_escaped = escape_markdown_v2(f"{avg:.0f} {currency}")
        new_content += f" \\(avg: {avg_escaped}\\)"

    # Find and replace the bids line
    lines = text.split('\n')
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(bids_emoji) and ('Bids:' in stripped or 'My bid:' in stripped):
            indent = line[:len(line) - len(stripped)]
            lines[i] = f"{indent}{bids_emoji} {new_content}"
            break

    return '\n'.join(lines)


async def schedule_bid_update(
    bot: Bot,
    chat_id,
    message_id: int,
    project_id: int,
    bid_id: int = None,
    bidding_service=None,
    currency: str = "USD",
    original_text: str = None,
    original_keyboard: InlineKeyboardMarkup = None,
    delay: int = 60,
):
    """Fetch updated bid stats after a delay and edit the bids line in-place.

    Runs as a background task (asyncio.create_task). After `delay` seconds,
    fetches fresh bid count, average, and position, then updates the existing
    bids line in the original message.

    If bid_id is None (variant 1 — info post), fetches stats without rank.
    """
    try:
        await asyncio.sleep(delay)

        if not original_text or not bidding_service:
            return

        rank = None
        total = None
        avg = None

        if bid_id:
            rank_info = bidding_service.get_bid_rank(bid_id, project_id, retry_delay=0)
            if rank_info:
                rank = rank_info.get("rank")
                total = rank_info.get("total_bids")
                avg = rank_info.get("avg_bid")
        else:
            stats = bidding_service.get_project_bid_stats(project_id)
            if stats:
                total = stats.get("total_bids")
                avg = stats.get("avg_bid")

        if total is not None:
            updated_text = replace_bids_line(
                original_text, rank=rank, total=total, avg=avg, currency=currency,
            )

            if updated_text != original_text:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=updated_text,
                        parse_mode="MarkdownV2",
                        reply_markup=original_keyboard,
                        disable_web_page_preview=True,
                    )
                    if rank:
                        logger.info(f"Bids line updated for {project_id}: #{rank} out of {total} (avg: {avg:.0f})")
                    else:
                        logger.info(f"Bids line updated for {project_id}: {total} bids (avg: {avg:.0f})")
                except Exception as edit_err:
                    logger.error(f"Failed to edit message for {project_id}: {edit_err}")
            else:
                logger.debug(f"Bids line unchanged for {project_id}")
        else:
            logger.debug(f"No updated stats for project {project_id}")

    except Exception as e:
        logger.error(f"Delayed bid update failed for {project_id}: {e}")


async def schedule_price_corrections(
    bot: Bot,
    chat_id,
    message_id: int,
    project_id: int,
    bid_id: int,
    bidding_service,
    currency: str,
    original_amount: float,
    days: int,
    min_daily_rate: int,
    original_text: str,
    original_keyboard: InlineKeyboardMarkup,
):
    """Через 60s и 600s: пересчёт цены и обновление бида.

    Также обновляет Telegram-сообщение (bids line + строка с изменением цены).
    """
    if not original_text or not bidding_service or not bid_id:
        return

    current_text = original_text
    current_amount = original_amount

    for delay in [60, 600]:
        sleep_time = delay if delay == 60 else 540  # 60s, затем ещё 540s = итого 10min
        await asyncio.sleep(sleep_time)

        try:
            # 1. Получить свежий avg_bid
            rank_info = bidding_service.get_bid_rank(bid_id, project_id, retry_delay=0)
            if not rank_info:
                continue

            avg_bid_currency = rank_info.get("avg_bid", 0)
            total = rank_info.get("total_bids")
            rank = rank_info.get("rank")

            # 2. Пересчитать в USD
            avg_bid_usd = to_usd(avg_bid_currency, currency) if currency != "USD" else avg_bid_currency
            floor_usd = days * min_daily_rate
            target_usd = avg_bid_usd * 0.90 if avg_bid_usd else 0

            # 3. Обновить bids line в сообщении
            if total is not None:
                current_text = replace_bids_line(current_text, rank=rank, total=total, avg=avg_bid_currency, currency=currency)

            # 4. Решить что делать с ценой
            price_line = None
            minutes = delay // 60

            if target_usd <= 0:
                pass  # нет данных avg — не меняем цену
            elif target_usd < floor_usd:
                # Отзываем бид — рынок ниже нашего минимума
                result = bidding_service.retract_bid(bid_id)
                if result.success:
                    price_line = f"🚫 _Bid retracted \\({minutes}min\\) — market avg below minimum_"
                    logger.info(f"Bid {bid_id} retracted at {minutes}min: avg ${avg_bid_usd:.0f} → target ${target_usd:.0f} < floor ${floor_usd:.0f}")
                else:
                    logger.error(f"Retract bid {bid_id} failed: {result.message}")
            else:
                # Пересчитать новую цену в валюте проекта
                new_amount_usd = round(target_usd / 10) * 10
                new_amount = round_up_10(from_usd(new_amount_usd, currency)) if currency != "USD" else new_amount_usd

                if new_amount < current_amount:
                    result = bidding_service.update_bid(bid_id, new_amount)
                    if result.success:
                        price_line = f"💰 _Price updated \\({minutes}min\\): {escape_markdown_v2(currency)} {escape_markdown_v2(f'{current_amount:.0f}')} → {escape_markdown_v2(f'{new_amount:.0f}')}_"
                        logger.info(f"Bid {bid_id} updated at {minutes}min: {current_amount:.0f} → {new_amount:.0f} {currency}")
                        current_amount = new_amount
                    else:
                        logger.error(f"Update bid {bid_id} failed: {result.message}")

            # 5. Добавить строку изменения цены в сообщение
            if price_line:
                current_text = current_text + f"\n{price_line}"

            # 6. Обновить сообщение в Telegram
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=current_text,
                    parse_mode="MarkdownV2",
                    reply_markup=original_keyboard,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.error(f"Failed to edit message for {project_id}: {e}")

            # Если бид отозван — дальше нет смысла
            if price_line and "retracted" in price_line:
                break

        except Exception as e:
            logger.error(f"Price correction failed for {project_id} at {delay//60}min: {e}")
