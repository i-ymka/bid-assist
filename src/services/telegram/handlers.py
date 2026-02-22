"""Telegram command and callback handlers."""

import logging
from datetime import datetime
import html
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import error as telegram_error
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    Application,
    filters,
)
from src.config import settings
from src.services.storage import ProjectRepository
from src.services.freelancer import FreelancerClient, BiddingService, ProjectService
from src.services.freelancer.bidding import strip_markdown
from src.services.telegram.notifier import create_updated_keyboard, rebuild_bid_message, ce, random_header_emoji
from src.models import Bid

logger = logging.getLogger(__name__)


# Conversation states
WAITING_AMOUNT, WAITING_TEXT = range(2)

# Runtime state (shared across handlers)
# Budget can be changed via /setbudget command
_runtime_state = {
    "paused": False,
    "min_budget": 50,  # Default: $50
    "max_budget": 3000,  # Default: $3000
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
    """Handle /start command - welcome message."""
    await update.message.reply_text(
        "👋 Welcome to *Bid-Assist*!\n\n"
        "I monitor Freelancer for new projects matching your skills "
        "and help you place bids quickly.\n\n"
        "Use /help to see all available commands.",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available commands."""
    help_text = """📚 *Available Commands*

*Status & Control*
/status — Status & Start/Stop bot
/bidstats — Bid history

*Settings*
/settings — Bot settings (budget, poll, auto-bid, filters)

*During Bid Edit*
/cancel — Cancel current edit
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


def _build_status_message(repo: ProjectRepository) -> str:
    """Build the status message text (HTML)."""
    from datetime import datetime

    state = get_runtime_state()
    queue_pending = repo.get_queue_count("pending")
    queue_analyzing = repo.get_queue_count("analyzing")
    poll_stats = repo.get_last_poll_stats()
    bot_start = repo.get_bot_start_time()
    auto_bid_status = "🟢 On" if repo.is_auto_bid() else "🔴 Off"

    # Stats: session vs all time
    session_stats = repo.get_bid_stats(since=bot_start)
    all_stats = repo.get_bid_stats()
    session_seen = repo.get_processed_count(since=bot_start)
    all_seen = repo.get_total_projects_seen()

    monitoring_status = "⏸️ PAUSED" if repo.is_paused() else "▶️ RUNNING"

    # Format uptime
    uptime_str = "unknown"
    if bot_start:
        try:
            start_time = datetime.fromisoformat(bot_start)
            uptime_seconds = int((datetime.now() - start_time).total_seconds())
            hours = uptime_seconds // 3600
            minutes = (uptime_seconds % 3600) // 60
            if hours > 0:
                uptime_str = f"{hours}h {minutes}m"
            else:
                uptime_str = f"{minutes}m"
        except Exception:
            pass

    # Format last poll info
    last_poll_info = ""
    if poll_stats:
        try:
            poll_time = datetime.fromisoformat(poll_stats["timestamp"])
            minutes_ago = int((datetime.now() - poll_time).total_seconds() / 60)
            if minutes_ago < 1:
                time_str = "just now"
            elif minutes_ago < 60:
                time_str = f"{minutes_ago}m ago"
            else:
                time_str = f"{minutes_ago // 60}h {minutes_ago % 60}m ago"

            last_poll_info = (
                f"\n<b>Last poll:</b> {time_str}\n"
                f"• Found: {poll_stats.get('found', 0)} projects\n"
                f"• Filtered: {poll_stats.get('filtered', 0)}\n"
                f"• Queued: {poll_stats.get('queued', 0)}\n"
                f"• Already bid: {poll_stats.get('already_bid', 0)}"
            )
        except Exception as e:
            logger.error(f"Error formatting poll stats: {e}")
            last_poll_info = "\n<b>Last poll:</b> unknown"

    # Format avg amount
    avg_str = f"${session_stats['avg_amount']}"
    if all_stats['avg_amount'] != session_stats['avg_amount']:
        avg_str += f" (${all_stats['avg_amount']})"

    return (
        f"📊 <b>Bid-Assist Status</b>\n\n"
        f"<b>Monitoring:</b> {monitoring_status}\n"
        f"<b>Auto-bid:</b> {auto_bid_status}\n"
        f"<b>Uptime:</b> {uptime_str}\n"
        f"<b>Queue:</b> {queue_pending} pending, {queue_analyzing} analyzing"
        f"{last_poll_info}\n\n"
        f"<b>📈 Statistics</b> <i>(session / all time)</i>\n"
        f"• Projects seen: {session_seen} ({all_seen})\n"
        f"• Bids placed: {session_stats['bids_placed']} ({all_stats['bids_placed']})\n"
        f"• Avg amount: {avg_str}"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command - show status + control buttons."""
    try:
        repo = ProjectRepository()
        message = _build_status_message(repo)
        keyboard = get_control_keyboard()
        await update.message.reply_text(message, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Error in /status command: {e}")
        await update.message.reply_text(f"❌ Error getting status: {e}")


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


async def cmd_setpoll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setpoll command - change poll interval."""
    repo = ProjectRepository()
    args = context.args

    current_interval = repo.get_poll_interval()

    if not args:
        await update.message.reply_text(
            f"⏱️ Current poll interval: {current_interval} seconds ({current_interval // 60} min)\n\n"
            "Usage: /setpoll <seconds>\n"
            "Example: /setpoll 60 (poll every minute)\n"
            "Example: /setpoll 300 (poll every 5 minutes)"
        )
        return

    try:
        seconds = int(args[0])

        if seconds < 30:
            await update.message.reply_text("❌ Minimum interval is 30 seconds")
            return
        if seconds > 3600:
            await update.message.reply_text("❌ Maximum interval is 3600 seconds (1 hour)")
            return

        repo.set_poll_interval(seconds)

        await update.message.reply_text(
            f"✅ Poll interval set to {seconds} seconds ({seconds // 60} min {seconds % 60}s)\n\n"
            f"Next poll cycle will use the new interval."
        )
        logger.info(f"Poll interval changed to {seconds}s via Telegram")

    except ValueError:
        await update.message.reply_text(
            "❌ Invalid number. Usage: /setpoll <seconds>"
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


async def send_in_chunks(update: Update, text: str, max_length: int = 4096):
    """Send a long message in chunks, with error handling."""
    if not text.strip():
        return
        
    try:
        if len(text) <= max_length:
            await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
            return

        messages = []
        current_message = ""
        for line in text.split('\n'):
            if len(current_message) + len(line) + 1 > max_length:
                messages.append(current_message)
                current_message = ""
            current_message += line + "\n"

        if current_message:
            messages.append(current_message)

        for msg in messages:
            await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)
            
    except (telegram_error.TimedOut, telegram_error.NetworkError) as e:
        logger.error(f"Failed to send message to Telegram due to network error: {e}")
        await update.message.reply_text(
            "❌ Failed to send the full response due to a network timeout. "
            "Please check your internet connection or try again later."
        )


def _fetch_bid_stats_sync(recent_bids: list) -> dict:
    """Fetch bid stats data synchronously (runs in thread pool).

    Takes sqlite3.Row objects from get_recent_bids_full().
    Uses DB data for static fields, API only for current status + winner info.

    Returns dict with structured data for dashboard + loss cards + compact summary.
    """
    from src.services.currency import to_usd

    project_service = ProjectService()
    client = FreelancerClient()
    my_user_id = client.get_user_id()

    wins = []
    losses_visible = []
    losses_sealed = []
    no_winner = []
    active = []
    error_count = 0
    winning_amounts_usd = []

    AWARDED_STATUSES = {"awarded", "complete", "accepted", "inprogress"}
    CLOSED_STATUSES = {"closed", "cancelled", "expired"}
    WINNER_AWARD_STATUSES = {"awarded", "accepted"}

    for row in recent_bids:
        project_id = row["project_id"]
        try:
            # Static data from DB (no API needed)
            title = row["title"] or f"Project {project_id}"
            url = row["url"] or f"https://www.freelancer.com/projects/{project_id}"
            our_amount = row["amount"] or 0
            our_proposal = row["description"] or ""
            currency = row["currency"] or "USD"
            budget_min = row["budget_min"] or 0
            budget_max = row["budget_max"] or 0
            bid_date_str = row["created_at"] or ""

            # Format date
            try:
                date_obj = datetime.fromisoformat(bid_date_str)
                date_fmt = date_obj.strftime("%d %b")
            except Exception:
                date_fmt = "?"

            # Format budget string
            if budget_min and budget_max:
                budget_str = f"{budget_min:.0f}-{budget_max:.0f} {currency}"
            elif budget_max:
                budget_str = f"up to {budget_max:.0f} {currency}"
            else:
                budget_str = "N/A"

            # API call 1: current project status
            project = project_service.get_project_details(project_id)
            if not project:
                error_count += 1
                continue

            # API call 2: bids to find winner
            bids, users = project_service.get_project_bids(project_id)
            winning_bid = next(
                (b for b in bids if b.get("award_status") in WINNER_AWARD_STATUSES),
                None,
            )

            # Classify outcome
            outcome = "OPEN"
            winner_amount = 0.0

            if winning_bid:
                winner_amount = winning_bid.get("amount", 0.0)

            if winning_bid and winner_amount > 0:
                outcome = "LOSS" if winning_bid.get("bidder_id") != my_user_id else "MY_WIN"
            elif project.status in AWARDED_STATUSES:
                my_bid = next((b for b in bids if b.get("bidder_id") == my_user_id), None)
                my_award = my_bid.get("award_status", "") if my_bid else ""
                if my_award in WINNER_AWARD_STATUSES:
                    outcome = "MY_WIN"
                else:
                    outcome = "LOSS_SEALED"
            elif project.status in CLOSED_STATUSES:
                outcome = "NO_WINNER"

            base = {"title": title, "url": url, "date": date_fmt, "currency": currency}

            if outcome == "MY_WIN":
                wins.append({**base, "amount": our_amount, "proposal": our_proposal})
                winning_amounts_usd.append(to_usd(our_amount, currency))

            elif outcome == "LOSS":
                winner_proposal = winning_bid.get("description", "") or ""
                winner_bidder_id = winning_bid.get("bidder_id")

                # API call 3: fetch winner's profile (rating, reviews, country)
                winner_profile = {}
                try:
                    resp = client.get(
                        f"/users/0.1/users/{winner_bidder_id}/",
                        params={"reputation": "true", "country_details": "true"},
                    )
                    wr = resp.get("result", {})
                    if wr:
                        rep = wr.get("reputation", {}).get("entire_history", {})
                        loc = wr.get("location", {})
                        winner_profile = {
                            "username": wr.get("username", ""),
                            "country": loc.get("country", {}).get("name", "N/A") if loc else "N/A",
                            "rating": rep.get("overall"),
                            "reviews": rep.get("reviews"),
                            "completion_rate": rep.get("completion_rate"),
                        }
                except Exception as e:
                    logger.warning(f"Could not fetch winner profile {winner_bidder_id}: {e}")

                losses_visible.append({
                    **base,
                    "budget_str": budget_str,
                    "our_amount": our_amount,
                    "our_proposal": our_proposal,
                    "winner_amount": winner_amount,
                    "winner_profile": winner_profile,
                    "winner_proposal": winner_proposal,
                })
                winning_amounts_usd.append(to_usd(winner_amount, currency))

            elif outcome == "LOSS_SEALED":
                losses_sealed.append({
                    **base,
                    "budget_str": budget_str,
                    "our_amount": our_amount,
                    "our_proposal": our_proposal,
                })

            elif outcome == "NO_WINNER":
                no_winner.append(base)

            else:  # OPEN
                active.append(base)

        except Exception as e:
            logger.error(f"Error processing bid stats for project {project_id}: {e}")
            error_count += 1

    # Calculate averages (normalize to USD for multi-currency)
    our_amounts_usd = []
    for row in recent_bids:
        amt = row["amount"]
        cur = row["currency"] or "USD"
        if amt:
            our_amounts_usd.append(to_usd(amt, cur))
    our_avg = sum(our_amounts_usd) / len(our_amounts_usd) if our_amounts_usd else 0
    avg_winning = sum(winning_amounts_usd) / len(winning_amounts_usd) if winning_amounts_usd else None

    return {
        "wins": wins,
        "losses_visible": losses_visible,
        "losses_sealed": losses_sealed,
        "no_winner": no_winner,
        "active": active,
        "errors": error_count,
        "avg_winning_bid": avg_winning,
        "our_avg_bid": our_avg,
        "total": len(recent_bids),
    }


_MAX_PROPOSAL_LEN = 400


def _build_dashboard_message(data: dict) -> str:
    """Build the dashboard summary message (Message 1)."""
    total = data["total"]
    win_count = len(data["wins"])
    vis_loss = len(data["losses_visible"])
    seal_loss = len(data["losses_sealed"])
    total_losses = vis_loss + seal_loss
    no_winner_count = len(data["no_winner"])
    active_count = len(data["active"])
    errors = data["errors"]

    def pct(n):
        return f"{n / total * 100:.0f}" if total else "0"

    decided = win_count + total_losses
    if decided:
        win_rate = f"{win_count / decided * 100:.0f}% ({win_count}/{decided} decided)"
    else:
        win_rate = "N/A"

    our_avg = data.get("our_avg_bid", 0)
    avg_win = data.get("avg_winning_bid")

    lines = [
        f"📊 <b>BID STATISTICS</b> (last {total} bids)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"✅ Won: {win_count} ({pct(win_count)}%)",
        f"❌ Lost: {total_losses} ({pct(total_losses)}%)",
    ]

    if total_losses > 0:
        lines.append(f"   ├ visible: {vis_loss}")
        lines.append(f"   └ sealed: {seal_loss}")

    lines.append(f"🚫 No winner: {no_winner_count} ({pct(no_winner_count)}%)")
    lines.append(f"⏳ Active: {active_count} ({pct(active_count)}%)")

    if errors:
        lines.append(f"⚠️ Errors: {errors}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 Your avg bid: ~${our_avg:.0f}")
    if avg_win is not None:
        lines.append(f"💰 Avg winning bid: ~${avg_win:.0f}")
    lines.append(f"🏆 Win rate: {win_rate}")

    return "\n".join(lines)


def _build_loss_card(loss: dict, is_sealed: bool = False) -> str:
    """Build a single loss analysis card."""
    title_esc = html.escape(loss["title"])
    url = loss["url"]
    title_link = f"<a href='{url}'>{title_esc}</a>"
    date = loss["date"]
    budget_str = loss.get("budget_str", "N/A")
    currency = loss.get("currency", "USD")

    our_amount = loss.get("our_amount", 0)
    our_proposal = html.escape(loss.get("our_proposal", ""))
    if len(our_proposal) > _MAX_PROPOSAL_LEN:
        our_proposal = our_proposal[:_MAX_PROPOSAL_LEN] + "..."

    lines = [
        f"❌ <b>{title_link}</b>",
        f"📅 {date} | Budget: {budget_str}",
        "",
        f"🙋 Your bid: {our_amount:.0f} {currency}",
    ]

    if our_proposal:
        lines.append(f"<blockquote>{our_proposal}</blockquote>")

    if is_sealed:
        lines.append("")
        lines.append("🔒 <i>Awarded to another (details hidden)</i>")
    else:
        winner_amount = loss.get("winner_amount", 0)
        wp = loss.get("winner_profile", {})
        winner_proposal = html.escape(loss.get("winner_proposal", ""))
        if len(winner_proposal) > _MAX_PROPOSAL_LEN:
            winner_proposal = winner_proposal[:_MAX_PROPOSAL_LEN] + "..."

        # Winner header with profile stats
        lines.append("")
        country = html.escape(wp.get("country", "N/A")) if wp else "N/A"
        winner_line = f"🏆 Winner: {winner_amount:.0f} {currency} · {country}"
        if wp:
            username = wp.get("username")
            if username:
                winner_line = f"🏆 Winner: @{html.escape(username)} · {winner_amount:.0f} {currency}"
            parts = []
            rating = wp.get("rating")
            if rating is not None:
                parts.append(f"⭐{rating:.1f}")
            reviews = wp.get("reviews")
            if reviews is not None:
                parts.append(f"{reviews} reviews")
            cr = wp.get("completion_rate")
            if cr is not None:
                parts.append(f"{cr * 100:.0f}%")
            if parts:
                winner_line += f"\n{country} · {' · '.join(parts)}"
        lines.append(winner_line)

        if winner_proposal:
            lines.append(f"<blockquote>{winner_proposal}</blockquote>")
        else:
            lines.append("<i>(No proposal text visible)</i>")

    return "\n".join(lines)


def _build_compact_summary(data: dict) -> str:
    """Build compact wins + active + closed summary (Message 3)."""
    lines = []

    if data["wins"]:
        lines.append(f"✅ <b>WINS ({len(data['wins'])})</b>")
        for w in data["wins"]:
            title_esc = html.escape(w["title"][:60])
            currency = w.get("currency", "USD")
            lines.append(f"• <a href='{w['url']}'>{title_esc}</a> — {w['amount']:.0f} {currency}")
        lines.append("")

    if data["active"]:
        lines.append(f"⏳ <b>ACTIVE ({len(data['active'])})</b>")
        for a in data["active"]:
            title_esc = html.escape(a["title"][:60])
            lines.append(f"• <a href='{a['url']}'>{title_esc}</a>")
        lines.append("")

    if data["no_winner"]:
        lines.append(f"🚫 <b>CLOSED — NO WINNER ({len(data['no_winner'])})</b>")
        for c in data["no_winner"]:
            title_esc = html.escape(c["title"][:60])
            lines.append(f"• <a href='{c['url']}'>{title_esc}</a>")

    return "\n".join(lines)


async def cmd_bid_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bidstats command — dashboard + loss analysis + compact summary."""
    await update.message.reply_text("⏳ Fetching bid statistics, please wait...")

    repo = ProjectRepository()
    recent_bids = repo.get_recent_bids_full(limit=25)

    if not recent_bids:
        await update.message.reply_text("No recent successful bids found.")
        return

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch_bid_stats_sync, recent_bids)

        # Message 1: Dashboard Summary
        dashboard = _build_dashboard_message(data)
        await update.message.reply_text(dashboard, parse_mode="HTML")

        # Message 2: Loss Analysis Cards (one per loss)
        all_losses = (
            [(loss, False) for loss in data["losses_visible"]]
            + [(loss, True) for loss in data["losses_sealed"]]
        )

        if all_losses:
            for loss, is_sealed in all_losses:
                try:
                    card = _build_loss_card(loss, is_sealed=is_sealed)
                    await update.message.reply_text(
                        card, parse_mode="HTML", disable_web_page_preview=True
                    )
                except telegram_error.TelegramError as e:
                    logger.error(f"Failed to send loss card: {e}")

        # Message 3: Wins + Active + Closed (compact)
        compact = _build_compact_summary(data)
        if compact:
            await send_in_chunks(update, compact, max_length=4000)

    except Exception as e:
        logger.error(f"Error in /bidstats: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error fetching bid stats: {e}")


# Budget presets: (min, max)
_BUDGET_PRESETS = [
    (30, 500),
    (50, 1000),
    (50, 2000),
    (50, 3000),
    (100, 5000),
]

# Poll interval presets (seconds)
_POLL_PRESETS = [60, 120, 180, 300, 600]


def _build_settings_message(repo: ProjectRepository) -> str:
    """Build the settings message text."""
    state = get_runtime_state()
    poll_sec = repo.get_poll_interval()
    poll_min = poll_sec // 60

    # Clear Yes/No labels
    crypto_status = "✅ Yes" if repo.is_verified() else "❌ No"
    preferred_status = "✅ Yes" if not repo.skip_preferred_only() else "❌ No"
    auto_bid_status = "✅ Yes" if repo.is_auto_bid() else "❌ No"
    skipped_status = "✅ Yes" if repo.get_receive_skipped() else "❌ No"

    return (
        f"⚙️ <b>Settings</b>\n\n"
        f"<b>Filters:</b>\n"
        f"• Budget: ${state['min_budget']} – ${state['max_budget']}\n"
        f"• Poll: every {poll_min}m ({poll_sec}s)\n"
        f"• Languages: {', '.join(settings.allowed_languages) if settings.allowed_languages else 'all'}\n"
        f"• Blocked currencies: {', '.join(settings.blocked_currencies) if settings.blocked_currencies else 'none'}\n\n"
        f"<b>Show projects:</b>\n"
        f"• Crypto (verified account): {crypto_status}\n"
        f"• Preferred-only: {preferred_status}\n\n"
        f"<b>Bidding:</b>\n"
        f"• Auto-bid: {auto_bid_status}\n\n"
        f"<b>Notifications:</b>\n"
        f"• Show skipped: {skipped_status}"
    )


def _get_settings_keyboard(repo: ProjectRepository) -> InlineKeyboardMarkup:
    """Create the keyboard for the settings message."""
    state = get_runtime_state()

    crypto_yn = "✅ Yes" if repo.is_verified() else "❌ No"
    preferred_yn = "✅ Yes" if not repo.skip_preferred_only() else "❌ No"
    auto_bid_yn = "✅ Yes" if repo.is_auto_bid() else "❌ No"
    skipped_yn = "✅ Yes" if repo.get_receive_skipped() else "❌ No"

    keyboard = [
        [
            InlineKeyboardButton(f"💰 Budget: ${state['min_budget']}–${state['max_budget']}", callback_data="settings:budget"),
            InlineKeyboardButton(f"⏱ Poll: {repo.get_poll_interval()}s", callback_data="settings:poll"),
        ],
        [
            InlineKeyboardButton(f"Crypto: {crypto_yn}", callback_data="settings:verified"),
            InlineKeyboardButton(f"Preferred: {preferred_yn}", callback_data="settings:skip_preferred"),
        ],
        [
            InlineKeyboardButton(f"Auto-bid: {auto_bid_yn}", callback_data="settings:auto_bid"),
            InlineKeyboardButton(f"Skipped: {skipped_yn}", callback_data="settings:skip_notif"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command - show all bot settings with interactive controls."""
    repo = ProjectRepository()
    message = _build_settings_message(repo)
    keyboard = _get_settings_keyboard(repo)
    await update.message.reply_text(message, parse_mode="HTML", reply_markup=keyboard)


async def handle_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callbacks from the /settings keyboard."""
    query = update.callback_query
    await query.answer()

    repo = ProjectRepository()
    action = query.data.split(":")[1]

    if action == "verified":
        current = repo.is_verified()
        repo.set_verified(not current)

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)

    elif action == "skip_preferred":
        current = repo.skip_preferred_only()
        repo.set_skip_preferred_only(not current)

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)

    elif action == "auto_bid":
        current = repo.is_auto_bid()
        repo.set_auto_bid(not current)

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)

    elif action == "budget":
        # Cycle through budget presets
        state = get_runtime_state()
        current = (state["min_budget"], state["max_budget"])
        try:
            idx = _BUDGET_PRESETS.index(current)
            next_idx = (idx + 1) % len(_BUDGET_PRESETS)
        except ValueError:
            next_idx = 0
        new_min, new_max = _BUDGET_PRESETS[next_idx]
        state["min_budget"] = new_min
        state["max_budget"] = new_max

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)

    elif action == "poll":
        # Cycle through poll presets
        current = repo.get_poll_interval()
        try:
            idx = _POLL_PRESETS.index(current)
            next_idx = (idx + 1) % len(_POLL_PRESETS)
        except ValueError:
            next_idx = 0
        repo.set_poll_interval(_POLL_PRESETS[next_idx])

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)

    elif action == "skip_notif":
        current = repo.get_receive_skipped()
        repo.set_receive_skipped(not current)

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)


async def cmd_setverified(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setverified command - toggle verified account status.

    Usage: /setverified on|off
           /setverified (show current)
    """
    repo = ProjectRepository()
    args = context.args

    current = repo.is_verified()

    if not args:
        status = "✅ Verified" if current else "❌ Not verified"
        keywords = ", ".join(settings.verification_keywords) if settings.verification_keywords else "(none)"

        await update.message.reply_text(
            f"🔒 <b>Account Verification Status</b>\n\n"
            f"Status: {status}\n"
            f"Filtered keywords: {keywords}\n\n"
            f"If not verified, projects with crypto/blockchain keywords are filtered out.\n\n"
            f"<b>Usage:</b>\n"
            f"<code>/setverified on</code> - I have verified account\n"
            f"<code>/setverified off</code> - Filter crypto projects",
            parse_mode="HTML"
        )
        return

    arg = args[0].lower()

    if arg in ("on", "true", "yes", "1"):
        repo.set_verified(True)
        await update.message.reply_text(
            "✅ Verified account: <b>ON</b>\n\n"
            "Crypto/blockchain projects will now be shown.",
            parse_mode="HTML"
        )
        logger.info("Verified account set to ON via Telegram")
    elif arg in ("off", "false", "no", "0"):
        repo.set_verified(False)
        await update.message.reply_text(
            "❌ Verified account: <b>OFF</b>\n\n"
            "Crypto/blockchain projects will be filtered out.",
            parse_mode="HTML"
        )
        logger.info("Verified account set to OFF via Telegram")
    else:
        await update.message.reply_text(
            "❌ Invalid value. Use: /setverified on or /setverified off"
        )


def get_control_keyboard() -> InlineKeyboardMarkup:
    """Get control panel keyboard based on current state."""
    repo = ProjectRepository()
    is_paused = repo.is_paused()

    if is_paused:
        button = InlineKeyboardButton("▶️ Start", callback_data="control:start")
    else:
        button = InlineKeyboardButton("⏹️ Stop", callback_data="control:stop")

    return InlineKeyboardMarkup([[button]])


async def cmd_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /control command - same as /status."""
    await cmd_status(update, context)


async def handle_control_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Start/Stop button clicks."""
    query = update.callback_query
    await query.answer()

    repo = ProjectRepository()
    action = query.data.split(":")[1]

    if action == "start":
        repo.set_paused(False)
        logger.info("Monitoring STARTED via control panel")
    else:  # stop
        repo.set_paused(True)
        logger.info("Monitoring STOPPED via control panel")

    # Refresh status message with control buttons
    message = _build_status_message(repo)
    keyboard = get_control_keyboard()
    await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)


async def handle_edit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Edit Amount' button click."""
    query = update.callback_query
    await query.answer()

    # Parse project_id from callback data
    try:
        project_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.message.reply_text("❌ Invalid data")
        return ConversationHandler.END

    # Check if bid still exists
    repo = ProjectRepository()
    bid_data = repo.get_pending_bid(project_id)
    if not bid_data:
        await query.message.reply_text("❌ Bid data expired.")
        return ConversationHandler.END

    # Store project_id in context for later use
    context.user_data["editing_project_id"] = project_id
    context.user_data["original_message"] = query.message

    currency = bid_data.get("currency", "USD")
    await query.message.reply_text(
        f"💵 Current amount: {bid_data['amount']:.0f} {currency}\n\n"
        f"Send new bid amount (number only):\n"
        f"Or send /cancel to cancel"
    )
    return WAITING_AMOUNT


async def receive_new_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new bid amount from user."""
    project_id = context.user_data.get("editing_project_id")
    original_message = context.user_data.get("original_message")

    if not project_id:
        await update.message.reply_text("❌ No edit in progress")
        return ConversationHandler.END

    try:
        new_amount = float(update.message.text.strip().replace("$", "").replace(",", ""))
        if new_amount <= 0:
            raise ValueError("Amount must be positive")
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Please send a number.\n"
            "Example: 150 or 150.50\n\n"
            "Or /cancel to cancel"
        )
        return WAITING_AMOUNT

    # Update the pending bid
    repo = ProjectRepository()
    bid_data = repo.update_pending_bid(project_id, amount=new_amount)
    if not bid_data:
        await update.message.reply_text("❌ Bid data expired.")
        return ConversationHandler.END

    currency = bid_data.get("currency", "USD")

    # Update the original message with new amount (full message + keyboard)
    try:
        new_text = rebuild_bid_message(bid_data)
        new_keyboard = create_updated_keyboard(project_id, new_amount, currency)
        await original_message.edit_text(
            text=new_text,
            parse_mode="MarkdownV2",
            reply_markup=new_keyboard,
            disable_web_page_preview=True,
        )
        logger.info(f"Updated original message with new amount: {new_amount} {currency}")
    except Exception as e:
        logger.error(f"Failed to update original message: {e}")
        # Fallback: at least update the keyboard
        try:
            new_keyboard = create_updated_keyboard(project_id, new_amount, currency)
            await original_message.edit_reply_markup(reply_markup=new_keyboard)
        except Exception as e2:
            logger.error(f"Failed to update keyboard: {e2}")

    await update.message.reply_text(
        f"✅ Amount updated to {new_amount:.0f} {currency}"
    )

    # Clear context
    context.user_data.pop("editing_project_id", None)
    context.user_data.pop("original_message", None)

    return ConversationHandler.END


async def handle_edit_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Edit Proposal' button click."""
    query = update.callback_query
    await query.answer()

    # Parse project_id from callback data
    try:
        project_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.message.reply_text("❌ Invalid data")
        return ConversationHandler.END

    # Check if bid still exists
    repo = ProjectRepository()
    bid_data = repo.get_pending_bid(project_id)
    if not bid_data:
        await query.message.reply_text("❌ Bid data expired.")
        return ConversationHandler.END

    # Store project_id in context for later use
    context.user_data["editing_project_id"] = project_id

    current_text = bid_data.get("description", "")  # Show full text

    # Store original message for updating later
    context.user_data["original_message"] = query.message

    await query.message.reply_text(
        f"📝 Current proposal:\n```\n{current_text}\n```\n\n"
        f"Send your new bid proposal text:\n"
        f"Or send /cancel to cancel",
        parse_mode="Markdown"
    )
    return WAITING_TEXT


async def receive_new_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new bid text from user."""
    project_id = context.user_data.get("editing_project_id")
    original_message = context.user_data.get("original_message")

    if not project_id:
        await update.message.reply_text("❌ No edit in progress")
        return ConversationHandler.END

    new_text = update.message.text.strip()
    if len(new_text) < 50:
        await update.message.reply_text(
            "❌ Proposal too short (min 50 characters).\n"
            "Please write a more detailed proposal.\n\n"
            "Or /cancel to cancel"
        )
        return WAITING_TEXT

    # Update the pending bid
    repo = ProjectRepository()
    bid_data = repo.update_pending_bid(project_id, description=new_text)
    if not bid_data:
        await update.message.reply_text("❌ Bid data expired.")
        return ConversationHandler.END

    currency = bid_data.get("currency", "USD")
    amount = bid_data.get("amount", 0)

    # Update the original message with new proposal (full message + keyboard)
    if original_message:
        try:
            new_message_text = rebuild_bid_message(bid_data)
            new_keyboard = create_updated_keyboard(project_id, amount, currency)
            await original_message.edit_text(
                text=new_message_text,
                parse_mode="MarkdownV2",
                reply_markup=new_keyboard,
                disable_web_page_preview=True,
            )
            logger.info(f"Updated original message with new proposal for project {project_id}")
        except Exception as e:
            logger.error(f"Failed to update original message: {e}")

    await update.message.reply_text(
        f"✅ Proposal updated!"
    )

    # Clear context
    context.user_data.pop("editing_project_id", None)
    context.user_data.pop("original_message", None)

    return ConversationHandler.END


async def cancel_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the edit operation."""
    context.user_data.pop("editing_project_id", None)
    context.user_data.pop("original_message", None)
    await update.message.reply_text("❌ Edit cancelled.")
    return ConversationHandler.END


async def handle_ask_bid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Ask for Bid Anyway' button click on skipped projects."""
    query = update.callback_query
    await query.answer()

    # Parse callback data: "ask_bid:{project_id}"
    data = query.data
    if not data.startswith("ask_bid:"):
        return

    try:
        project_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ Invalid project data")
        return

    # Get project data from queue
    repo = ProjectRepository()
    project_data = repo.get_project_from_queue(project_id)
    if not project_data:
        await query.edit_message_text(
            "❌ Project data not found. It may have been too long since the skip."
        )
        return

    # Store original skip message text
    original_text = query.message.text_markdown_v2 if query.message.text_markdown_v2 else query.message.text

    # Edit skip message to show we're generating bid
    from src.services.telegram.notifier import escape_markdown_v2
    await query.edit_message_text(
        original_text + "\n\n⏳ _Generating bid\\.\\.\\._",
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )

    # Import and call force_bid_analysis
    import asyncio
    from src.services.ai.gemini_analyzer import force_bid_analysis

    # Format budget string (convert to USD for AI)
    from src.services.currency import to_usd, from_usd, round_up_10

    budget_min = project_data.get("budget_min", 0)
    budget_max = project_data.get("budget_max", 0)
    currency = project_data.get("currency", "USD")
    url = project_data.get("url", "")
    bid_count = project_data.get("bid_count", 0)
    avg_bid = project_data.get("avg_bid", 0)

    budget_min_usd = to_usd(budget_min, currency) if budget_min else 0
    budget_max_usd = to_usd(budget_max, currency) if budget_max else 0
    avg_bid_usd = to_usd(avg_bid, currency) if avg_bid else 0

    if budget_min_usd and budget_max_usd:
        budget_str = f"{budget_min_usd:.0f} - {budget_max_usd:.0f} USD"
    elif budget_max_usd:
        budget_str = f"up to {budget_max_usd:.0f} USD"
    else:
        budget_str = "Not specified"

    # Run analysis in thread pool (it's blocking)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        force_bid_analysis,
        project_id,
        project_data["title"],
        project_data["description"],
        budget_str,
        avg_bid_usd,
        bid_count,
    )

    if not result:
        # Restore original message with error
        await query.edit_message_text(
            original_text + "\n\n❌ _AI analysis failed\\. Try again later\\._",
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )
        return

    # Convert AI's USD amount back to project currency
    if currency != "USD" and result.amount > 0:
        result.amount = round_up_10(from_usd(result.amount, currency))
        logger.info(f"Force bid amount converted to {result.amount} {currency}")

    # Store as pending bid (with all context for later edits/retry)
    repo.add_pending_bid(
        project_id=project_id,
        amount=result.amount,
        period=result.period,
        description=result.bid_text,
        title=project_data["title"],
        currency=currency,
        url=url,
        bid_count=bid_count,
        summary=result.summary,
        budget_min=budget_min,
        budget_max=budget_max,
        client_country=project_data.get("client_country", ""),
        avg_bid=avg_bid,
    )

    # Update skip message to show "Asked for bid anyway ↓"
    await query.edit_message_text(
        original_text + "\n\n🔄 _Asked for bid anyway ↓_",
        parse_mode="MarkdownV2",
        disable_web_page_preview=True,
    )

    # Reply with bid info (no summary - context is in parent message)
    # Clean markdown from bid text before displaying
    bid_text_clean = strip_markdown(result.bid_text)
    bid_text_escaped = escape_markdown_v2(bid_text_clean)
    currency_escaped = escape_markdown_v2(currency)

    reply_text = (
        f"💡 *AI Generated Bid:*\n"
        f"  {ce('budget')} Amount: {result.amount:.0f} {currency_escaped} for {result.period} days\n\n"
        f"{ce('proposal')} *Bid Proposal:*\n```\n{bid_text_escaped}\n```"
    )

    # Create bid buttons
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
        f"💰 Place Bid ({result.amount:.0f} {currency})",
        callback_data=f"bid:{project_id}",
        api_kwargs={"style": "success"},
    )
    keyboard = InlineKeyboardMarkup([
        [edit_amount_btn, edit_text_btn],
        [bid_btn]
    ])

    # Reply to the skip message with bid info
    await query.message.reply_text(
        reply_text,
        parse_mode="MarkdownV2",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )

    logger.info(f"Force bid generated for project {project_id}: {result.amount} {currency}")


async def handle_bid_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Place Bid' button click."""
    query = update.callback_query

    # Parse callback data: "bid:{project_id}"
    data = query.data
    if not data.startswith("bid:"):
        await query.answer()
        return

    try:
        project_id = int(data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("❌ Invalid bid data", show_alert=True)
        return

    # Get pending bid data (this is the CURRENT data - might have been edited by teammate)
    repo = ProjectRepository()
    bid_data = repo.get_pending_bid(project_id)
    if not bid_data:
        await query.answer("❌ Bid data expired", show_alert=True)
        return

    # Lazy sync: refresh message with latest data (in case teammate edited)
    # This ensures user sees current proposal/amount before placing bid
    try:
        new_text = rebuild_bid_message(bid_data)
        new_keyboard = create_updated_keyboard(
            project_id,
            bid_data["amount"],
            bid_data.get("currency", "USD")
        )
        await query.message.edit_text(
            text=new_text,
            parse_mode="MarkdownV2",
            reply_markup=new_keyboard,
            disable_web_page_preview=True,
        )
    except Exception as e:
        # If message is unchanged or edit fails, continue anyway
        logger.debug(f"Could not refresh message (may be unchanged): {e}")

    # Check if bid was already placed by another user
    if repo.is_project_bidded(project_id):
        await query.answer("Bid already placed by teammate!", show_alert=True)

        # Get URL for "Check my bid" button
        url = bid_data.get("url", "")
        check_bid_url = f"{url}/proposals" if url else ""

        from src.services.telegram.notifier import escape_markdown_v2
        status_text = "\n\n✅ *Bid already placed by teammate\\!*"

        keyboard = None
        if check_bid_url:
            check_btn = InlineKeyboardButton(
                "🔗 Check my bid",
                url=check_bid_url,
                api_kwargs={"style": "primary"},
            )
            keyboard = InlineKeyboardMarkup([[check_btn]])

        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception as e:
            logger.warning(f"Could not update keyboard: {e}")
        return

    # Show loading indicator via callback answer (doesn't modify message)
    await query.answer("⏳ Placing bid...")

    # Place the bid
    bidding_service = get_bidding_service()

    bid = Bid(
        project_id=project_id,
        amount=bid_data["amount"],
        period=bid_data["period"],
        milestone_percentage=settings.default_milestone_pct,
        description=bid_data["description"],
    )

    result = bidding_service.place_bid(bid)

    # Get data before removing from pending
    currency = bid_data.get("currency", "USD")
    url = bid_data.get("url", "")
    bid_count = bid_data.get("bid_count", 0)

    # Update existing pending_manual record or create new one
    repo.update_bid_record_on_place(
        project_id=project_id,
        amount=bid_data["amount"],
        period=bid_data["period"],
        description=bid_data["description"],
        success=result.success,
        error_message=result.message if not result.success else None,
        notification_sent=True,
    )

    # Remove from pending
    repo.remove_pending_bid(project_id)

    # Update message with result
    if result.success:
        # Get rank info and remaining bids immediately after placing bid
        rank_info = None
        remaining_bids = None
        if result.bid_id:
            try:
                rank_info = bidding_service.get_bid_rank(result.bid_id, project_id, retry_delay=1.0)
            except Exception:
                pass
            try:
                remaining_bids = bidding_service.get_remaining_bids()
            except Exception:
                pass

        # Build "Check my bid" URL
        check_bid_url = f"{url}/proposals" if url else ""
        keyboard = None
        if check_bid_url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Check my bid", url=check_bid_url, api_kwargs={"style": "primary"})]
            ])

        # Build variant 2 message from scratch
        from src.services.telegram.notifier import build_bid_placed_message
        try:
            placed_text = build_bid_placed_message(bid_data, rank_info, remaining_bids)

            await query.edit_message_text(
                text=placed_text,
                parse_mode="MarkdownV2",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to update message with bid result: {e}")
            try:
                original_text = query.message.text or ""
                bid_result_text = (
                    f"\n\n{'─' * 30}\n"
                    f"BID PLACED!\n"
                    f"{bid_data['amount']:.0f} {currency} · {bid_data['period']} days\n"
                )
                await query.edit_message_text(
                    text=original_text + bid_result_text,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            except Exception as e2:
                logger.error(f"Fallback also failed: {e2}")
                try:
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                except:
                    pass

        # Schedule delayed update (1 min) with fresh bid count, avg, position
        if result.bid_id:
            import asyncio
            from src.services.telegram.notifier import schedule_bid_update
            try:
                edited_text = placed_text
            except Exception:
                edited_text = None
            asyncio.create_task(
                schedule_bid_update(
                    bot=context.bot,
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    project_id=project_id,
                    bid_id=result.bid_id,
                    bidding_service=bidding_service,
                    currency=currency,
                    original_text=edited_text,
                    original_keyboard=keyboard,
                )
            )

        logger.info(f"Bid placed on project {project_id}: {bid_data['amount']} {currency}")
    else:
        # On failure, show error as alert
        from src.services.telegram.notifier import escape_markdown_v2

        # Keep edit buttons so user can try again
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
        retry_btn = InlineKeyboardButton(
            f"🔄 Retry Bid",
            callback_data=f"bid:{project_id}",
            api_kwargs={"style": "danger"},
            )
        keyboard = InlineKeyboardMarkup([
            [edit_amount_btn, edit_text_btn],
            [retry_btn]
        ])

        # Re-add to pending bids for retry (preserve all context)
        repo.add_pending_bid(
            project_id=project_id,
            amount=bid_data["amount"],
            period=bid_data["period"],
            description=bid_data["description"],
            title=bid_data["title"],
            currency=currency,
            url=url,
            bid_count=bid_count,
            summary=bid_data.get("summary"),
            budget_min=bid_data.get("budget_min"),
            budget_max=bid_data.get("budget_max"),
            client_country=bid_data.get("client_country"),
            avg_bid=bid_data.get("avg_bid"),
        )

        # Check for common errors and provide helpful messages
        error_msg = result.message
        help_text = "You can edit and retry\\."

        if "used all" in error_msg.lower() or "all of your bids" in error_msg.lower():
            help_text = (
                "⚠️ You've used all your bids on Freelancer\\.\n"
                "Purchase more or wait for your limit to reset\\."
            )
        elif "language" in error_msg.lower():
            help_text = (
                "⚠️ *Fix:* Go to Freelancer\\.com → Settings → Browse Projects → "
                "Add the project's language \\(e\\.g\\. Spanish\\)\\.\n\n"
                "Then retry the bid\\."
            )

        # Reply with error
        try:
            await query.message.reply_text(
                f"❌ *Bid failed*\n\n"
                f"Error: {escape_markdown_v2(error_msg)}\n\n"
                f"{help_text}",
                parse_mode="MarkdownV2",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to send error message: {e}")

        logger.error(f"Bid failed on project {project_id}: {result.message}")


async def handle_emoji_extract(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Extract custom emoji IDs from messages containing custom emoji."""
    if not update.message or not update.message.entities:
        return

    custom_emojis = [
        e for e in update.message.entities
        if e.type == "custom_emoji"
    ]

    if not custom_emojis:
        return

    lines = []
    for entity in custom_emojis:
        emoji_char = update.message.text[entity.offset:entity.offset + entity.length]
        lines.append(f"{emoji_char}  →  <code>{entity.custom_emoji_id}</code>")

    await update.message.reply_text(
        f"🔍 <b>Custom Emoji IDs</b>\n\n" + "\n".join(lines),
        parse_mode="HTML",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler to catch exceptions."""
    if isinstance(context.error, telegram_error.NetworkError):
        logger.warning(f"Network error encountered: {context.error}")
    else:
        logger.error(msg="Exception while handling an update:", exc_info=context.error)


def setup_handlers(application: Application):
    """Register all handlers with the application."""
    # Command handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("control", cmd_control))
    application.add_handler(CommandHandler("pause", cmd_pause))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("bidstats", cmd_bid_stats))
    application.add_handler(CommandHandler("settings", cmd_settings))

    # Legacy commands kept for backwards compatibility but hidden from menu
    application.add_handler(CommandHandler("setbudget", cmd_setbudget))
    application.add_handler(CommandHandler("setpoll", cmd_setpoll))
    application.add_handler(CommandHandler("setverified", cmd_setverified))

    # Settings callbacks
    application.add_handler(CallbackQueryHandler(handle_settings_callback, pattern="^settings:"))

    # Control panel Start/Stop callbacks
    application.add_handler(CallbackQueryHandler(handle_control_callback, pattern="^control:"))

    # Conversation handler for editing amount
    edit_amount_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_edit_amount, pattern="^edit_amount:")
        ],
        states={
            WAITING_AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_amount),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit)],
        per_message=False,
    )

    # Conversation handler for editing proposal text
    edit_text_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_edit_text, pattern="^edit_text:")
        ],
        states={
            WAITING_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit)],
        per_message=False,
    )

    application.add_handler(edit_amount_handler)
    application.add_handler(edit_text_handler)

    # Callback handler for "Ask for Bid" button (on skipped projects)
    application.add_handler(CallbackQueryHandler(handle_ask_bid_callback, pattern="^ask_bid:"))

    # Callback handler for Bid button
    application.add_handler(CallbackQueryHandler(handle_bid_callback, pattern="^bid:"))

    # Custom emoji ID extractor (must be last — catches all text messages with entities)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_emoji_extract))

    # Global error handler
    application.add_error_handler(error_handler)

    logger.info("Telegram handlers registered")
