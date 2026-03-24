"""Telegram command and callback handlers."""

import asyncio
import logging
import warnings
warnings.filterwarnings("ignore", message=".*per_message.*", category=UserWarning)
from datetime import datetime, timedelta
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
from src.services.telegram.notifier import create_updated_keyboard, rebuild_bid_message, ce, random_header_emoji, escape_markdown_v2
from src.models import Bid

logger = logging.getLogger(__name__)


# Conversation states
WAITING_AMOUNT, WAITING_TEXT, WAITING_SPINNER = range(3)

# Runtime state (shared across handlers)
# Budget is persisted in runtime_settings DB; load stored values at import time.
_runtime_state = {
    "paused": False,
    "min_budget": 50,  # Default: $50
    "max_budget": 3000,  # Default: $3000
}
try:
    _init_repo = ProjectRepository()
    _bmin, _bmax = _init_repo.get_budget_range()
    _runtime_state["min_budget"] = _bmin
    _runtime_state["max_budget"] = _bmax
except Exception:
    pass  # DB not ready yet — will use defaults

# Singleton services
_bidding_service = None
_project_service = None


def get_bidding_service() -> BiddingService:
    """Get or create bidding service."""
    global _bidding_service
    if _bidding_service is None:
        client = FreelancerClient()
        _bidding_service = BiddingService(client)
    return _bidding_service


def get_project_service() -> ProjectService:
    """Get or create project service."""
    global _project_service
    if _project_service is None:
        client = FreelancerClient()
        _project_service = ProjectService(client)
    return _project_service


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
            uptime_seconds = int((datetime.utcnow() - start_time).total_seconds())
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
        ProjectRepository().set_budget_range(min_budget, max_budget)

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


_AWARDED_STATUSES = {"awarded", "complete", "accepted", "inprogress"}
_CLOSED_STATUSES = {"closed", "cancelled", "expired"}
_WINNER_AWARD_STATUSES = {"awarded", "accepted"}
# Outcomes that are final (don't need re-checking)
_FINAL_OUTCOMES = {"MY_WIN", "LOSS", "LOSS_SEALED", "NO_WINNER", "ERROR"}


def _classify_project(project_id, project_service, client, my_user_id):
    """Classify a single project's outcome via API. Returns (outcome, detail_dict or None)."""
    project = project_service.get_project_details(project_id)
    if not project:
        return None, None

    bids, users = project_service.get_project_bids(project_id)
    winning_bid = next(
        (b for b in bids if b.get("award_status") in _WINNER_AWARD_STATUSES),
        None,
    )

    outcome = "OPEN"
    detail = None

    if winning_bid:
        winner_amount = winning_bid.get("amount", 0.0)
        if winning_bid.get("bidder_id") == my_user_id:
            outcome = "MY_WIN"
        elif winner_amount > 0:
            outcome = "LOSS"
            winner_user_id = winning_bid.get("bidder_id")
            # Fetch winner profile (extended fields)
            winner_profile = {}
            winner_hourly_rate = None
            winner_reg_date = None
            winner_earnings_score = None
            winner_portfolio_count = None
            try:
                resp = client.get(
                    f"/users/0.1/users/{winner_user_id}/",
                    params={
                        "reputation": "true",
                        "country_details": "true",
                        "hourly_rate": "true",
                        "registration_date": "true",
                        "earnings": "true",
                    },
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
                    winner_hourly_rate = wr.get("hourly_rate")
                    winner_reg_date = wr.get("registration_date")
                    winner_earnings_score = rep.get("earnings_score")
            except Exception as e:
                logger.warning(f"Could not fetch winner profile: {e}")
            # Portfolio count (separate API call)
            try:
                winner_portfolio_count = project_service.get_portfolio_count(winner_user_id)
            except Exception as e:
                logger.debug(f"Portfolio count fetch failed for winner {winner_user_id}: {e}")
            # Time to bid calculations
            project_ts = project.time_submitted.timestamp() if project.time_submitted else None
            winner_bid_ts = winning_bid.get("submitdate")
            winner_time_to_bid_sec = None
            if project_ts and winner_bid_ts:
                winner_time_to_bid_sec = int(winner_bid_ts - project_ts)
            # My bid timing
            my_bid = next((b for b in bids if b.get("bidder_id") == my_user_id), None)
            my_time_to_bid_sec = None
            if my_bid and project_ts:
                my_bid_ts = my_bid.get("submitdate")
                if my_bid_ts:
                    my_time_to_bid_sec = int(my_bid_ts - project_ts)
            detail = {
                "winner_amount": winner_amount,
                "winner_profile": winner_profile,
                "winner_proposal": winning_bid.get("description", "") or "",
                "winner_hourly_rate": winner_hourly_rate,
                "winner_reg_date": winner_reg_date,
                "winner_earnings_score": winner_earnings_score,
                "winner_portfolio_count": winner_portfolio_count,
                "my_time_to_bid_sec": my_time_to_bid_sec,
                "winner_time_to_bid_sec": winner_time_to_bid_sec,
            }
        else:
            outcome = "LOSS_SEALED"
    elif project.status in _AWARDED_STATUSES:
        my_bid = next((b for b in bids if b.get("bidder_id") == my_user_id), None)
        my_award = my_bid.get("award_status", "") if my_bid else ""
        if my_award in _WINNER_AWARD_STATUSES:
            outcome = "MY_WIN"
        else:
            outcome = "LOSS_SEALED"
    elif project.status in _CLOSED_STATUSES:
        outcome = "NO_WINNER"

    return outcome, detail


_stats_cache = {"data": None, "ts": None}
_STATS_CACHE_TTL = 1800  # 30 minutes


def _fetch_bid_stats_sync() -> dict:
    """Fetch ALL user bids from Freelancer API, classify, build stats.

    Uses in-memory cache (30 min) + DB outcome cache.
    Source: Freelancer API (includes manual bids), NOT bid_history.
    """
    now = datetime.now()
    if (
        _stats_cache["data"] is not None
        and _stats_cache["ts"]
        and (now - _stats_cache["ts"]).total_seconds() < _STATS_CACHE_TTL
    ):
        logger.info("Bidstats: returning cached result (age: %ds)", int((now - _stats_cache["ts"]).total_seconds()))
        return _stats_cache["data"]

    bidding_service = get_bidding_service()
    project_service = ProjectService()
    client = FreelancerClient()
    my_user_id = client.get_user_id()
    repo = ProjectRepository()

    # Fetch my own profile once (extended fields for My Profile header)
    my_profile = {}
    try:
        resp = client.get(
            f"/users/0.1/users/{my_user_id}/",
            params={
                "reputation": "true",
                "country_details": "true",
                "hourly_rate": "true",
                "registration_date": "true",
                "earnings": "true",
            },
        )
        mr = resp.get("result", {})
        if mr:
            rep = mr.get("reputation", {}).get("entire_history", {})
            loc = mr.get("location", {})
            reg_date = mr.get("registration_date")
            import time as _time
            years_on = round((_time.time() - reg_date) / (365.25 * 86400), 1) if reg_date else None
            my_profile = {
                "username": mr.get("username", ""),
                "country": loc.get("country", {}).get("name", "N/A") if loc else "N/A",
                "rating": rep.get("overall"),
                "reviews": rep.get("reviews"),
                "completion_rate": rep.get("completion_rate"),
                "hourly_rate": mr.get("hourly_rate"),
                "years_on_platform": years_on,
                "earnings_score": rep.get("earnings_score"),
                "portfolio_count": project_service.get_portfolio_count(my_user_id),
                "bid_adjustment": repo.get_bid_adjustment(),
                "min_daily_rate": repo.get_min_daily_rate(),
                "prompts_dir": settings.prompts_dir,
            }
    except Exception as e:
        logger.warning(f"Could not fetch own profile: {e}")

    # Fetch ALL bids from Freelancer API (paginated, includes manual bids)
    api_bids = bidding_service.get_all_my_bids()

    # Pre-load bid_history for project details (title, url, currency, budget)
    bid_history_rows = repo.get_recent_bids_full()
    bh_lookup = {}
    for row in bid_history_rows:
        bh_lookup[row["project_id"]] = row

    wins = []
    losses_visible = []
    losses_sealed = []
    no_winner = []
    active = []
    error_count = 0
    classify_calls = 0

    for bid in api_bids:
        project_id = bid.get("project_id")
        if not project_id:
            continue
        try:
            # Data from API bid object
            our_amount = bid.get("amount", 0) or 0
            our_proposal = bid.get("description", "") or ""
            submit_ts = bid.get("submitdate", 0)
            award_status = bid.get("award_status", "")

            # Project details from bid_history DB (bot-placed bids)
            bh = bh_lookup.get(project_id, {})
            title = (bh["title"] if bh else None) or f"Project {project_id}"
            url = (bh["url"] if bh else None) or f"https://www.freelancer.com/projects/{project_id}"
            currency = (bh["currency"] if bh else None) or "USD"
            budget_min = (bh["budget_min"] if bh else None) or 0
            budget_max = (bh["budget_max"] if bh else None) or 0

            # Format date from unix timestamp
            try:
                date_obj = datetime.fromtimestamp(submit_ts)
                date_fmt = date_obj.strftime("%d %b")
                date_iso = date_obj.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                date_fmt = "?"
                date_iso = ""

            # Format budget string
            if budget_min and budget_max:
                budget_str = f"{budget_min:.0f}-{budget_max:.0f} {currency}"
            elif budget_max:
                budget_str = f"up to {budget_max:.0f} {currency}"
            else:
                budget_str = "N/A"

            base = {"title": title, "url": url, "date": date_fmt, "currency": currency, "created_at_raw": date_iso}

            # Fast path: award_status tells us if WE won
            if award_status in _WINNER_AWARD_STATUSES:
                wins.append({**base, "amount": our_amount, "proposal": our_proposal})
                repo.set_bid_outcome(project_id, "MY_WIN")
                continue

            # Check DB cache
            cached_row = repo.get_bid_outcome_full(project_id)
            detail = None

            if cached_row and cached_row["outcome"] in _FINAL_OUTCOMES:
                cached_outcome = cached_row["outcome"]
                if cached_outcome == "ERROR":
                    error_count += 1
                    continue
                outcome = cached_outcome
                # For LOSS: load or backfill winner comparison data for accurate averages.
                # Without this, comparison metrics are computed only over freshly-classified
                # losses (tiny non-representative sample) → wildly inconsistent numbers.
                if outcome == "LOSS":
                    ca = cached_row.get("winner_amount")
                    cp = cached_row.get("winner_proposal")
                    cpl = cached_row.get("winner_proposal_len")
                    cr = cached_row.get("winner_reviews")
                    if ca is not None or cpl is not None or cr is not None:
                        # Reconstruct a detail-compatible dict from cached values.
                        # Use stored text if available; fall back to length placeholder for metrics only.
                        detail = {
                            "winner_amount": ca or 0,
                            "winner_proposal": cp if cp is not None else "x" * (cpl or 0),
                            "winner_profile": {"reviews": cr},
                            "winner_hourly_rate": cached_row.get("winner_hourly_rate"),
                            "winner_reg_date": cached_row.get("winner_reg_date"),
                            "winner_earnings_score": cached_row.get("winner_earnings_score"),
                            "winner_portfolio_count": cached_row.get("winner_portfolio_count"),
                            "my_time_to_bid_sec": cached_row.get("my_time_to_bid_sec"),
                            "winner_time_to_bid_sec": cached_row.get("winner_time_to_bid_sec"),
                        }
                    else:
                        # No winner data yet (rows classified before this fix) — re-classify once to backfill.
                        _, fresh_detail = _classify_project(project_id, project_service, client, my_user_id)
                        classify_calls += 1
                        if fresh_detail:
                            repo.set_bid_outcome(project_id, "LOSS", fresh_detail)
                            detail = fresh_detail
            elif bid.get("frontend_bid_status") == "active":
                # Still active — skip API call
                outcome = "OPEN"
            else:
                # Closed, not won by us — need API to determine LOSS vs SEALED vs NO_WINNER
                outcome, detail = _classify_project(project_id, project_service, client, my_user_id)
                classify_calls += 1
                if outcome is None:
                    error_count += 1
                    repo.set_bid_outcome(project_id, "ERROR")
                    continue
                repo.set_bid_outcome(project_id, outcome, detail)

            if outcome == "MY_WIN":
                wins.append({**base, "amount": our_amount, "proposal": our_proposal})
            elif outcome == "LOSS":
                entry = {**base, "budget_str": budget_str, "our_amount": our_amount, "our_proposal": our_proposal}
                if detail:
                    entry.update(detail)
                losses_visible.append(entry)
            elif outcome == "LOSS_SEALED":
                losses_sealed.append({**base, "budget_str": budget_str, "our_amount": our_amount, "our_proposal": our_proposal})
            elif outcome == "NO_WINNER":
                no_winner.append(base)
            else:
                active.append(base)

        except Exception as e:
            logger.error(f"Error processing bid stats for project {project_id}: {e}")
            error_count += 1

    # Comparison metrics (visible losses with winner data)
    price_diffs_pct = []
    proposal_diffs = []
    review_diffs = []
    my_reviews = my_profile.get("reviews")

    for loss in losses_visible:
        winner_amount = loss.get("winner_amount", 0)
        if winner_amount > 0 and loss.get("our_amount", 0) > 0:
            price_diffs_pct.append((loss["our_amount"] / winner_amount - 1) * 100)

        our_len = len(loss.get("our_proposal", ""))
        winner_len = len(loss.get("winner_proposal", ""))
        if our_len > 0 or winner_len > 0:
            proposal_diffs.append(our_len - winner_len)

        winner_reviews = (loss.get("winner_profile") or {}).get("reviews")
        if winner_reviews is not None and my_reviews is not None:
            review_diffs.append(my_reviews - winner_reviews)

    comparison = {}
    if price_diffs_pct:
        comparison["avg_price_diff_pct"] = sum(price_diffs_pct) / len(price_diffs_pct)
    if proposal_diffs:
        comparison["avg_proposal_diff_chars"] = sum(proposal_diffs) / len(proposal_diffs)
    if review_diffs:
        comparison["avg_review_diff"] = sum(review_diffs) / len(review_diffs)

    logger.info(f"Bidstats: {len(api_bids)} bids, {classify_calls} classify calls, {error_count} errors")

    result = {
        "wins": wins,
        "losses_visible": losses_visible,
        "losses_sealed": losses_sealed,
        "no_winner": no_winner,
        "active": active,
        "errors": error_count,
        "comparison": comparison,
        "my_profile": my_profile,
        "total": len(api_bids),
    }

    _stats_cache["data"] = result
    _stats_cache["ts"] = datetime.now()

    return result


_MAX_PROPOSAL_LEN = 400


def _build_dashboard_message(data: dict) -> str:
    """Build dashboard summary with counts, percentages, win rate and comparison metrics."""
    total = data["total"]
    wins = len(data["wins"])
    losses_v = len(data["losses_visible"])
    losses_s = len(data["losses_sealed"])
    total_losses = losses_v + losses_s
    no_win = len(data["no_winner"])
    active_n = len(data["active"])
    decided = wins + total_losses

    def pct(n):
        return f"{n / total * 100:.0f}" if total else "0"

    win_rate = f"{wins / decided * 100:.0f}% ({wins}/{decided})" if decided else "N/A"

    lines = [
        f"📊 <b>BID STATISTICS</b> ({total} bids)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"✅ Won: {wins} ({pct(wins)}%)",
    ]

    loss_line = f"❌ Lost: {total_losses} ({pct(total_losses)}%)"
    if total_losses > 0 and (losses_v > 0 or losses_s > 0):
        loss_line += f"  [{losses_v} visible, {losses_s} sealed]"
    lines.append(loss_line)
    lines.append(f"🚫 No winner: {no_win} ({pct(no_win)}%)")
    lines.append(f"⏳ Active: {active_n} ({pct(active_n)}%)")

    if data.get("errors"):
        lines.append(f"⚠️ Errors: {data['errors']}")

    lines.append(f"🏆 Win rate: {win_rate}")

    # Comparison metrics (vs winners in visible losses)
    comp = data.get("comparison", {})
    if comp:
        lines.append("")
        lines.append("<b>You vs Winners (in lost projects):</b>")

        price_diff = comp.get("avg_price_diff_pct")
        if price_diff is not None:
            if price_diff >= 0:
                lines.append(f"💰 Your bid is <b>{price_diff:.0f}% higher</b>")
            else:
                lines.append(f"💰 Your bid is <b>{abs(price_diff):.0f}% lower</b>")

        prop_diff = comp.get("avg_proposal_diff_chars")
        if prop_diff is not None:
            if prop_diff >= 0:
                lines.append(f"📝 Your proposal is <b>{prop_diff:.0f} chars longer</b>")
            else:
                lines.append(f"📝 Your proposal is <b>{abs(prop_diff):.0f} chars shorter</b>")

        rev_diff = comp.get("avg_review_diff")
        if rev_diff is not None:
            if rev_diff >= 0:
                lines.append(f"⭐ You have <b>{rev_diff:.0f} more</b> reviews")
            else:
                lines.append(f"⭐ You have <b>{abs(rev_diff):.0f} fewer</b> reviews")

    return "\n".join(lines)


def _build_loss_card(loss: dict, is_sealed: bool = False, my_profile: dict = None) -> str:
    """Build a single loss analysis card with symmetric YOU vs WINNER format."""
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

    def _profile_line(profile: dict) -> str:
        """Format: Country · ⭐4.9 · 12 reviews · 95%"""
        if not profile:
            return ""
        parts = []
        country = profile.get("country")
        if country:
            parts.append(html.escape(country))
        rating = profile.get("rating")
        if rating is not None:
            parts.append(f"⭐{rating:.1f}")
        reviews = profile.get("reviews")
        if reviews is not None:
            parts.append(f"{reviews} reviews")
        cr = profile.get("completion_rate")
        if cr is not None:
            parts.append(f"{cr * 100:.0f}%")
        return " · ".join(parts)

    lines = [
        f"❌ <b>{title_link}</b>",
        f"📅 {date} | Budget: {budget_str}",
    ]

    # — YOU —
    lines.append("")
    my_header = f"🙋 YOU: {our_amount:.0f} {currency}"
    my_stats = _profile_line(my_profile) if my_profile else ""
    if my_stats:
        my_header += f"\n{my_stats}"
    lines.append(my_header)
    if our_proposal:
        lines.append(f"<blockquote>{our_proposal}</blockquote>")

    # — TIMING —
    def _fmt_time(secs):
        if secs is None:
            return None
        secs = max(0, secs)
        if secs < 3600:
            return f"{secs // 60}min"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"

    my_ttb = _fmt_time(loss.get("my_time_to_bid_sec"))
    win_ttb = _fmt_time(loss.get("winner_time_to_bid_sec"))
    if my_ttb or win_ttb:
        timing_line = f"⏱ You: {my_ttb or '?'} | Winner: {win_ttb or '?'}"
        lines.append(timing_line)

    # — WINNER —
    if is_sealed:
        lines.append("")
        lines.append("🔒 <i>Awarded to another (details hidden)</i>")
    else:
        wp = loss.get("winner_profile", {})
        winner_amount = loss.get("winner_amount", 0)
        winner_proposal = html.escape(loss.get("winner_proposal", ""))
        if len(winner_proposal) > _MAX_PROPOSAL_LEN:
            winner_proposal = winner_proposal[:_MAX_PROPOSAL_LEN] + "..."

        lines.append("")
        winner_header = f"🏆 WINNER: {winner_amount:.0f} {currency}"
        if wp and wp.get("username"):
            winner_header = f"🏆 WINNER @{html.escape(wp['username'])}: {winner_amount:.0f} {currency}"
        winner_stats = _profile_line(wp)
        if winner_stats:
            winner_header += f"\n{winner_stats}"
        lines.append(winner_header)

        # Extended winner profile
        ext_parts = []
        hourly = loss.get("winner_hourly_rate")
        if hourly is not None:
            ext_parts.append(f"${hourly:.0f}/hr")
        reg_date = loss.get("winner_reg_date")
        if reg_date:
            import time as _time
            years = (_time.time() - reg_date) / (365.25 * 86400)
            ext_parts.append(f"{years:.1f}yr on platform")
        escore = loss.get("winner_earnings_score")
        if escore is not None:
            ext_parts.append(f"earnings {escore:.1f}/10")
        pcount = loss.get("winner_portfolio_count")
        if pcount is not None:
            ext_parts.append(f"portfolio: {pcount}")
        if ext_parts:
            lines.append(" · ".join(ext_parts))

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


def _build_weekly_subset(data: dict, cutoff_str: str) -> dict:
    """Filter classified data to entries created after cutoff. Recompute comparison metrics."""

    def _after(lst):
        return [e for e in lst if e.get("created_at_raw", "") >= cutoff_str]

    losses_visible = _after(data["losses_visible"])

    weekly = {
        "wins": _after(data["wins"]),
        "losses_visible": losses_visible,
        "losses_sealed": _after(data["losses_sealed"]),
        "no_winner": _after(data["no_winner"]),
        "active": _after(data["active"]),
        "errors": 0,
        "my_profile": data.get("my_profile", {}),
    }
    weekly["total"] = sum(
        len(weekly[k]) for k in ["wins", "losses_visible", "losses_sealed", "no_winner", "active"]
    )

    # Recompute comparison metrics for the weekly window
    my_reviews = weekly["my_profile"].get("reviews")
    price_diffs = []
    proposal_diffs = []
    review_diffs = []

    for loss in losses_visible:
        wa = loss.get("winner_amount", 0)
        oa = loss.get("our_amount", 0)
        if wa > 0 and oa > 0:
            price_diffs.append((oa / wa - 1) * 100)

        our_len = len(loss.get("our_proposal", ""))
        win_len = len(loss.get("winner_proposal", ""))
        if our_len > 0 or win_len > 0:
            proposal_diffs.append(our_len - win_len)

        wr = (loss.get("winner_profile") or {}).get("reviews")
        if wr is not None and my_reviews is not None:
            review_diffs.append(my_reviews - wr)

    comparison = {}
    if price_diffs:
        comparison["avg_price_diff_pct"] = sum(price_diffs) / len(price_diffs)
    if proposal_diffs:
        comparison["avg_proposal_diff_chars"] = sum(proposal_diffs) / len(proposal_diffs)
    if review_diffs:
        comparison["avg_review_diff"] = sum(review_diffs) / len(review_diffs)
    weekly["comparison"] = comparison

    return weekly


_MAX_LOSS_CARDS = 10


async def cmd_bid_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /bidstats — show period picker buttons."""
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 All time", callback_data="bidstats:alltime"),
            InlineKeyboardButton("📅 Last 7 days", callback_data="bidstats:weekly"),
        ]
    ])
    await update.message.reply_text("Choose period:", reply_markup=keyboard)


async def handle_bidstats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bidstats period selection and more_losses pagination."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]  # "alltime", "weekly", or "more_losses"

    # Handle "Show more" losses pagination
    if action == "more_losses":
        period = parts[2]
        offset = int(parts[3])
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _fetch_bid_stats_sync)
        except Exception as e:
            await query.message.reply_text(f"❌ Error: {e}")
            return

        if period == "weekly":
            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            subset = _build_weekly_subset(data, cutoff)
        else:
            subset = data
        all_losses = (
            [(loss, False) for loss in subset.get("losses_visible", [])]
            + [(loss, True) for loss in subset.get("losses_sealed", [])]
        )
        my_profile = data.get("my_profile", {})
        batch = all_losses[offset:offset + _MAX_LOSS_CARDS]
        for loss, is_sealed in batch:
            try:
                card = _build_loss_card(loss, is_sealed=is_sealed, my_profile=my_profile)
                await query.message.reply_text(card, parse_mode="HTML", disable_web_page_preview=True)
            except telegram_error.TelegramError as e:
                logger.error(f"Failed to send loss card: {e}")

        new_offset = offset + _MAX_LOSS_CARDS
        if new_offset < len(all_losses):
            await query.message.reply_text(
                f"<i>Showing {new_offset} of {len(all_losses)} losses</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        f"Show more ↓ ({len(all_losses) - new_offset} left)",
                        callback_data=f"bidstats:more_losses:{period}:{new_offset}"
                    )
                ]])
            )
        else:
            await query.message.reply_text(
                f"<i>Showing all {len(all_losses)} losses</i>",
                parse_mode="HTML",
            )
        return

    # Handle "Analyse week" callback (T012)
    if action == "analyse_week":
        period = parts[2] if len(parts) > 2 else "weekly"
        await query.message.reply_text("⏳ Analysing your bids...")
        try:
            import asyncio
            from src.services.ai.gemini_analyzer import analyse_weekly_bids
            from src.services.github import post_issue
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _fetch_bid_stats_sync)

            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            weekly_data = _build_weekly_subset(data, cutoff)
            my_profile = data.get("my_profile", {})

            # Build wins/losses lists for analysis
            wins_for_analysis = []
            for w in weekly_data.get("wins", []):
                wins_for_analysis.append({
                    "title": w.get("title", ""),
                    "amount": w.get("amount", 0),
                    "bid_text": w.get("proposal", ""),
                    "my_time_to_bid_sec": w.get("my_time_to_bid_sec"),
                })
            losses_for_analysis = []
            for lo in weekly_data.get("losses_visible", []):
                losses_for_analysis.append({
                    "title": lo.get("title", ""),
                    "my_amount": lo.get("our_amount", 0),
                    "winner_amount": lo.get("winner_amount", 0),
                    "bid_text": lo.get("our_proposal", ""),
                    "my_time_to_bid_sec": lo.get("my_time_to_bid_sec"),
                    "winner_time_to_bid_sec": lo.get("winner_time_to_bid_sec"),
                    "winner_reviews": (lo.get("winner_profile") or {}).get("reviews"),
                    "winner_hourly_rate": lo.get("winner_hourly_rate"),
                    "winner_reg_date": lo.get("winner_reg_date"),
                    "winner_earnings_score": lo.get("winner_earnings_score"),
                    "winner_portfolio_count": lo.get("winner_portfolio_count"),
                })

            analysis_text = await loop.run_in_executor(
                None, analyse_weekly_bids, wins_for_analysis, losses_for_analysis, my_profile
            )

            if not analysis_text:
                await query.message.reply_text("❌ AI analysis failed. Try again later.")
                return

            # Send analysis to Telegram (split if > 4096 chars)
            MAX_MSG = 4096
            chunks = [analysis_text[i:i + MAX_MSG] for i in range(0, len(analysis_text), MAX_MSG)]
            for chunk in chunks:
                await query.message.reply_text(chunk)

            # Post to GitHub Issues
            from datetime import date
            week_str = date.today().strftime("%Y-%m-%d")
            username = my_profile.get("username", "user")
            issue_title = f"[AI Analysis] Week of {week_str} — @{username}"
            issue_body = f"**Period:** last 7 days  \n**Account:** @{username}\n\n{analysis_text}"
            issue_url = await loop.run_in_executor(
                None, post_issue,
                settings.github_token, settings.github_repo,
                issue_title, issue_body, ["ai-analysis"]
            )

            if issue_url:
                await query.message.reply_text(f"🔗 GitHub Issue: {issue_url}")
                # Append to PROMPT_LOG.md
                try:
                    log_path = "docs/PROMPT_LOG.md"
                    import os
                    if os.path.exists(log_path):
                        issue_num = issue_url.rstrip("/").split("/")[-1]
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n- [Issue #{issue_num}]({issue_url}) — {week_str}\n")
                except Exception as log_err:
                    logger.warning(f"Could not append to PROMPT_LOG.md: {log_err}")

        except Exception as e:
            logger.error(f"Error in analyse_week: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Analysis error: {e}")
        return

    period = parts[1]  # "alltime" or "weekly"

    await query.edit_message_text("⏳ Fetching bid statistics, please wait...")

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, _fetch_bid_stats_sync)

        if data["total"] == 0:
            await query.edit_message_text("No bids found.")
            return

        if period == "alltime":
            # All time: only dashboard summary
            dashboard = _build_dashboard_message(data)
            await query.edit_message_text(dashboard, parse_mode="HTML")

        else:
            # Weekly: My profile header + dashboard + loss cards + compact + Analyse button
            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            weekly_data = _build_weekly_subset(data, cutoff)

            dashboard = _build_dashboard_message(weekly_data)
            await query.edit_message_text(dashboard, parse_mode="HTML")

            # My profile header (T008)
            my_profile = data.get("my_profile", {})
            if my_profile:
                profile_lines = [f"👤 <b>My profile: @{html.escape(my_profile.get('username', '?'))}</b>"]
                p_parts = []
                country = my_profile.get("country")
                if country:
                    p_parts.append(html.escape(country))
                rating = my_profile.get("rating")
                if rating is not None:
                    p_parts.append(f"⭐{rating:.1f}")
                reviews = my_profile.get("reviews")
                if reviews is not None:
                    p_parts.append(f"{reviews} reviews")
                cr = my_profile.get("completion_rate")
                if cr is not None:
                    p_parts.append(f"{cr * 100:.0f}%")
                if p_parts:
                    profile_lines.append(" · ".join(p_parts))
                ext_parts = []
                hourly = my_profile.get("hourly_rate")
                if hourly is not None:
                    ext_parts.append(f"${hourly:.0f}/hr")
                yrs = my_profile.get("years_on_platform")
                if yrs is not None:
                    ext_parts.append(f"{yrs}yr on platform")
                escore = my_profile.get("earnings_score")
                if escore is not None:
                    ext_parts.append(f"earnings {escore:.1f}/10")
                pcount = my_profile.get("portfolio_count")
                if pcount is not None:
                    ext_parts.append(f"portfolio: {pcount}")
                if ext_parts:
                    profile_lines.append(" · ".join(ext_parts))
                settings_parts = []
                adj = my_profile.get("bid_adjustment")
                if adj is not None:
                    settings_parts.append(f"bid adj: {adj:+d}%")
                mdr = my_profile.get("min_daily_rate")
                if mdr is not None:
                    settings_parts.append(f"min ${mdr}/day")
                if settings_parts:
                    profile_lines.append("⚙️ " + " · ".join(settings_parts))
                await query.message.reply_text("\n".join(profile_lines), parse_mode="HTML")

            # Loss cards (most recent N)
            all_losses = (
                [(loss, False) for loss in weekly_data["losses_visible"]]
                + [(loss, True) for loss in weekly_data["losses_sealed"]]
            )

            if all_losses:
                shown = all_losses[:_MAX_LOSS_CARDS]
                for loss, is_sealed in shown:
                    try:
                        card = _build_loss_card(loss, is_sealed=is_sealed, my_profile=my_profile)
                        await query.message.reply_text(
                            card, parse_mode="HTML", disable_web_page_preview=True
                        )
                    except telegram_error.TelegramError as e:
                        logger.error(f"Failed to send loss card: {e}")
                if len(all_losses) > _MAX_LOSS_CARDS:
                    remaining = len(all_losses) - _MAX_LOSS_CARDS
                    await query.message.reply_text(
                        f"<i>Showing {_MAX_LOSS_CARDS} of {len(all_losses)} losses</i>",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                f"Show more ↓ ({remaining} left)",
                                callback_data=f"bidstats:more_losses:weekly:{_MAX_LOSS_CARDS}"
                            )
                        ]])
                    )

            # Compact summary
            compact = _build_compact_summary(weekly_data)
            if compact:
                await query.message.reply_text(compact, parse_mode="HTML", disable_web_page_preview=True)

            # Analyse week button (T009)
            await query.message.reply_text(
                "Ready to analyse this week's bids?",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 Analyse week", callback_data="bidstats:analyse_week:weekly")
                ]])
            )

    except Exception as e:
        logger.error(f"Error in /bidstats: {e}", exc_info=True)
        await query.edit_message_text(f"❌ Error fetching bid stats: {e}")


# Spinner config: key → (label, unit, min, max, step_small, step_big)
_SPINNER_CONFIG = {
    "bid_adj":    ("Bid Adjustment",  "%",      -50, 50,    5,  10),
    "max_bids":   ("Max Competitors", " bids",    1, 999,   5,  25),
    "daily_rate": ("Min Daily Rate",  "$/day",   25, 500,  25,  50),
    "poll":       ("Poll Interval",   "s",       30, 3600, 30,  60),
    "budget_min": ("Min Budget",      "$",       30, 10000, 50, 200),
    "budget_max": ("Max Budget",      "$",       30, 10000, 50, 200),
}


def _spinner_get(repo: "ProjectRepository", key: str) -> int:
    state = get_runtime_state()
    if key == "bid_adj":    return repo.get_bid_adjustment()
    if key == "max_bids":   return repo.get_max_bid_count()
    if key == "daily_rate": return repo.get_min_daily_rate()
    if key == "poll":       return repo.get_poll_interval()
    if key == "budget_min": return state["min_budget"]
    if key == "budget_max": return state["max_budget"]
    return 0


def _spinner_set(repo: "ProjectRepository", key: str, value: int) -> None:
    state = get_runtime_state()
    if key == "bid_adj":    repo.set_bid_adjustment(value)
    elif key == "max_bids":   repo.set_max_bid_count(value)
    elif key == "daily_rate": repo.set_min_daily_rate(value)
    elif key == "poll":       repo.set_poll_interval(value)
    elif key == "budget_min":
        state["min_budget"] = value
        repo.set_budget_range(value, state["max_budget"])
    elif key == "budget_max":
        state["max_budget"] = value
        repo.set_budget_range(state["min_budget"], value)


def _build_spinner_message(key: str, value: int) -> str:
    label, unit, min_val, max_val, _, _ = _SPINNER_CONFIG[key]
    sign = "+" if value > 0 else ""
    return (
        f"⚙️ <b>{label}</b>\n\n"
        f"Value: <b>{sign}{value}{unit}</b>\n\n"
        f"Range: {min_val}…{max_val}{unit}"
    )


def _build_spinner_keyboard(key: str, value: int) -> InlineKeyboardMarkup:
    _, unit, min_val, max_val, step_s, step_b = _SPINNER_CONFIG[key]
    sign = "+" if value > 0 else ""
    display = f"{sign}{value}{unit}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"−{step_b}", callback_data=f"spinner:{key}:-{step_b}"),
            InlineKeyboardButton(f"−{step_s}", callback_data=f"spinner:{key}:-{step_s}"),
            InlineKeyboardButton("✏️",         callback_data=f"spinput:{key}"),
            InlineKeyboardButton(f"+{step_s}", callback_data=f"spinner:{key}:+{step_s}"),
            InlineKeyboardButton(f"+{step_b}", callback_data=f"spinner:{key}:+{step_b}"),
        ],
        [
            InlineKeyboardButton(display, callback_data=f"spinner:{key}:0"),
            InlineKeyboardButton("✅ Done", callback_data="spinner:done"),
        ],
    ])


def _build_settings_message(repo: ProjectRepository) -> str:
    """Build the settings message text."""
    state = get_runtime_state()
    poll_sec = repo.get_poll_interval()
    poll_min = poll_sec // 60

    # Clear Yes/No labels
    verified_status = "✅ Yes" if repo.is_verified() else "❌ No"
    preferred_status = "✅ Yes" if not repo.skip_preferred_only() else "❌ No"
    auto_bid_status = "✅ Yes" if repo.is_auto_bid() else "❌ No"
    skipped_status = "✅ Yes" if repo.get_receive_skipped() else "❌ No"

    min_daily_rate = repo.get_min_daily_rate()
    bid_adjustment = repo.get_bid_adjustment()
    adj_sign = "+" if bid_adjustment > 0 else ""
    return (
        f"⚙️ <b>Settings</b>\n\n"
        f"<b>Filters:</b>\n"
        f"• Budget: ${state['min_budget']} – ${state['max_budget']}\n"
        f"• Poll: every {poll_min}m ({poll_sec}s)\n"
        f"• Languages: {', '.join(settings.allowed_languages) if settings.allowed_languages else 'all'}\n"
        f"• Blocked currencies: {', '.join(settings.blocked_currencies) if settings.blocked_currencies else 'none'}\n\n"
        f"<b>Show projects:</b>\n"
        f"• Verified account: {verified_status}\n"
        f"• Preferred-only: {preferred_status}\n\n"
        f"<b>Bidding:</b>\n"
        f"• Auto-bid: {auto_bid_status}\n"
        f"• Min daily rate: ${min_daily_rate}/day\n"
        f"• Bid adjustment: {adj_sign}{bid_adjustment}% from market\n\n"
        f"<b>Notifications:</b>\n"
        f"• Show skipped: {skipped_status}"
    )


def _get_settings_keyboard(repo: ProjectRepository) -> InlineKeyboardMarkup:
    """Create the keyboard for the settings message."""
    state = get_runtime_state()

    verified_yn = "✅ Yes" if repo.is_verified() else "❌ No"
    preferred_yn = "✅ Yes" if not repo.skip_preferred_only() else "❌ No"
    auto_bid_yn = "✅ Yes" if repo.is_auto_bid() else "❌ No"
    skipped_yn = "✅ Yes" if repo.get_receive_skipped() else "❌ No"

    min_daily_rate = repo.get_min_daily_rate()
    bid_adjustment = repo.get_bid_adjustment()
    adj_sign = "+" if bid_adjustment > 0 else ""
    poll_sec = repo.get_poll_interval()
    poll_display = f"{poll_sec // 60}m" if poll_sec % 60 == 0 else f"{poll_sec}s"
    keyboard = [
        [
            InlineKeyboardButton(f"💰 Min: ${state['min_budget']}", callback_data="settings:budget_min"),
            InlineKeyboardButton(f"💰 Max: ${state['max_budget']}", callback_data="settings:budget_max"),
            InlineKeyboardButton(f"⏱ Poll: {poll_display}", callback_data="settings:poll"),
        ],
        [
            InlineKeyboardButton(f"Verified: {verified_yn}", callback_data="settings:verified"),
            InlineKeyboardButton(f"Preferred: {preferred_yn}", callback_data="settings:skip_preferred"),
        ],
        [
            InlineKeyboardButton(f"Auto-bid: {auto_bid_yn}", callback_data="settings:auto_bid"),
            InlineKeyboardButton(f"Min rate: ${min_daily_rate}/day", callback_data="settings:daily_rate"),
        ],
        [
            InlineKeyboardButton(f"Bid adj: {adj_sign}{bid_adjustment}%", callback_data="settings:bid_adj"),
            InlineKeyboardButton(f"Max bids: {repo.get_max_bid_count()}", callback_data="settings:max_bids"),
        ],
        [
            InlineKeyboardButton(f"Show skipped: {skipped_yn}", callback_data="settings:skip_notif"),
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

    elif action in _SPINNER_CONFIG:
        value = _spinner_get(repo, action)
        await query.edit_message_text(
            _build_spinner_message(action, value),
            parse_mode="HTML",
            reply_markup=_build_spinner_keyboard(action, value),
        )

    elif action == "skip_notif":
        current = repo.get_receive_skipped()
        repo.set_receive_skipped(not current)

        message = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await query.edit_message_text(message, parse_mode="HTML", reply_markup=keyboard)


async def handle_spinner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle spinner +/− buttons and Done for numeric settings."""
    query = update.callback_query
    await query.answer()

    repo = ProjectRepository()
    parts = query.data.split(":")  # ["spinner", key, delta] or ["spinner", "done"]

    if parts[1] == "done":
        await query.edit_message_text(
            _build_settings_message(repo),
            parse_mode="HTML",
            reply_markup=_get_settings_keyboard(repo),
        )
        return

    key = parts[1]
    delta = int(parts[2])
    _, _, min_val, max_val, _, _ = _SPINNER_CONFIG[key]

    current = _spinner_get(repo, key)
    new_value = max(min_val, min(max_val, current + delta))
    if new_value != current:
        _spinner_set(repo, key, new_value)

    await query.edit_message_text(
        _build_spinner_message(key, new_value),
        parse_mode="HTML",
        reply_markup=_build_spinner_keyboard(key, new_value),
    )


async def handle_spinput_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open keyboard input mode for a spinner setting."""
    query = update.callback_query
    await query.answer()

    key = query.data.split(":")[1]
    label, unit, min_val, max_val, _, _ = _SPINNER_CONFIG[key]
    repo = ProjectRepository()
    current = _spinner_get(repo, key)
    sign = "+" if current > 0 else ""

    context.user_data["spinner_key"] = key
    context.user_data["spinner_message_id"] = query.message.message_id

    await query.edit_message_text(
        f"⌨️ <b>{label}</b>\n\n"
        f"Current: <b>{sign}{current}{unit}</b>\n"
        f"Type a number from {min_val} to {max_val} and send:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"spincancel:{key}")]
        ]),
    )
    return WAITING_SPINNER


async def receive_spinner_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive typed value, validate, save, return to spinner."""
    key = context.user_data.get("spinner_key")
    msg_id = context.user_data.get("spinner_message_id")
    if not key:
        return ConversationHandler.END

    _, unit, min_val, max_val, _, _ = _SPINNER_CONFIG[key]

    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        value = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text(f"❌ Enter a whole number ({min_val}…{max_val})", parse_mode="HTML")
        return WAITING_SPINNER

    if not (min_val <= value <= max_val):
        await update.message.reply_text(f"❌ Must be between {min_val} and {max_val}", parse_mode="HTML")
        return WAITING_SPINNER

    repo = ProjectRepository()
    _spinner_set(repo, key, value)

    context.user_data.pop("spinner_key", None)
    context.user_data.pop("spinner_message_id", None)

    await context.bot.edit_message_text(
        chat_id=update.message.chat_id,
        message_id=msg_id,
        text=_build_spinner_message(key, value),
        parse_mode="HTML",
        reply_markup=_build_spinner_keyboard(key, value),
    )
    return ConversationHandler.END


async def handle_spincancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel keyboard input, return to spinner without saving."""
    query = update.callback_query
    await query.answer()

    key = query.data.split(":")[1]
    context.user_data.pop("spinner_key", None)
    context.user_data.pop("spinner_message_id", None)

    repo = ProjectRepository()
    current = _spinner_get(repo, key)
    await query.edit_message_text(
        _build_spinner_message(key, current),
        parse_mode="HTML",
        reply_markup=_build_spinner_keyboard(key, current),
    )
    return ConversationHandler.END


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
            f"If not verified, projects with verification-required keywords are filtered out.\n\n"
            f"<b>Usage:</b>\n"
            f"<code>/setverified on</code> - I have verified account\n"
            f"<code>/setverified off</code> - Filter verification-required projects",
            parse_mode="HTML"
        )
        return

    arg = args[0].lower()

    if arg in ("on", "true", "yes", "1"):
        repo.set_verified(True)
        await update.message.reply_text(
            "✅ Verified account: <b>ON</b>\n\n"
            "Verification-required projects will now be shown.",
            parse_mode="HTML"
        )
        logger.info("Verified account set to ON via Telegram")
    elif arg in ("off", "false", "no", "0"):
        repo.set_verified(False)
        await update.message.reply_text(
            "❌ Verified account: <b>OFF</b>\n\n"
            "Verification-required projects will be filtered out.",
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

    min_daily_rate = repo.get_min_daily_rate()
    bid_adjustment = repo.get_bid_adjustment()

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
        budget_min_usd,
        budget_max_usd,
        min_daily_rate,
        project_data.get("owner_display_name") or project_data.get("owner_username", ""),
        bid_adjustment,
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

    # Last-mile competitor check: fresh bid_count from API right before placing
    max_bids_now = repo.get_max_bid_count()
    loop = asyncio.get_event_loop()
    fresh_project = await loop.run_in_executor(
        None, get_project_service().get_project_details, project_id
    )
    if not fresh_project:
        await query.answer("⚠️ Project unavailable", show_alert=True)
        warning_text = rebuild_bid_message(bid_data) + escape_markdown_v2(
            "\n\n⚠️ Could not verify project status — it may have been deleted or closed."
        )
        try:
            await query.edit_message_text(
                text=warning_text,
                parse_mode="MarkdownV2",
                disable_web_page_preview=True,
            )
        except Exception:
            pass
        return
    fresh_bid_count = fresh_project.bid_stats.bid_count
    if fresh_bid_count > max_bids_now:
        await query.answer("⚠️ Too many competitors", show_alert=True)
        force_btn = InlineKeyboardButton(
            f"⚠️ Bid anyway ({fresh_bid_count} competitors)",
            callback_data=f"bid_force:{project_id}",
        )
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
        warning_text = rebuild_bid_message(bid_data) + escape_markdown_v2(
            f"\n\n⚠️ Project now has {fresh_bid_count} bids (your limit: {max_bids_now})."
        )
        try:
            await query.edit_message_text(
                text=warning_text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup([
                    [edit_amount_btn, edit_text_btn],
                    [force_btn],
                ]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Failed to edit message with competitor warning: {e}")
        return

    # Show loading indicator via callback answer (doesn't modify message)
    await query.answer("⏳ Placing bid...")
    await _execute_bid_placement(query, project_id, bid_data, repo, context)


async def _execute_bid_placement(query, project_id: int, bid_data: dict, repo: ProjectRepository, context):
    """Place a bid and update the message with the result."""
    bidding_service = get_bidding_service()

    bid = Bid(
        project_id=project_id,
        amount=bid_data["amount"],
        period=bid_data["period"],
        milestone_percentage=settings.default_milestone_pct,
        description=bid_data["description"],
    )

    result = bidding_service.place_bid(bid)

    currency = bid_data.get("currency", "USD")
    url = bid_data.get("url", "")
    bid_count = bid_data.get("bid_count", 0)

    repo.update_bid_record_on_place(
        project_id=project_id,
        amount=bid_data["amount"],
        period=bid_data["period"],
        description=bid_data["description"],
        success=result.success,
        error_message=result.message if not result.success else None,
        notification_sent=True,
    )

    repo.remove_pending_bid(project_id)

    if result.success:
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

        check_bid_url = f"{url}/proposals" if url else ""
        keyboard = None
        if check_bid_url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Check my bid", url=check_bid_url, api_kwargs={"style": "primary"})]
            ])

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
                except Exception:
                    pass

        if result.bid_id:
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
            "🔄 Retry Bid",
            callback_data=f"bid:{project_id}",
            api_kwargs={"style": "danger"},
        )
        keyboard = InlineKeyboardMarkup([
            [edit_amount_btn, edit_text_btn],
            [retry_btn]
        ])

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


async def handle_bid_force_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Bid anyway' button — place bid bypassing competitor limit."""
    query = update.callback_query

    try:
        project_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("❌ Invalid data", show_alert=True)
        return

    repo = ProjectRepository()
    bid_data = repo.get_pending_bid(project_id)
    if not bid_data:
        await query.answer("❌ Bid data expired", show_alert=True)
        return

    if repo.is_project_bidded(project_id):
        await query.answer("Bid already placed by teammate!", show_alert=True)
        return

    await query.answer("⏳ Placing bid...")
    await _execute_bid_placement(query, project_id, bid_data, repo, context)


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
    application.add_handler(CallbackQueryHandler(handle_spinner_callback, pattern="^spinner:"))

    # Spinner keyboard input
    spinner_input_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_spinput_callback, pattern="^spinput:")],
        states={
            WAITING_SPINNER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_spinner_value),
                CallbackQueryHandler(handle_spincancel_callback, pattern="^spincancel:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_edit)],
        per_message=False,
    )
    application.add_handler(spinner_input_handler)

    # Control panel Start/Stop callbacks
    application.add_handler(CallbackQueryHandler(handle_control_callback, pattern="^control:"))

    # Bid stats period selection
    application.add_handler(CallbackQueryHandler(handle_bidstats_callback, pattern="^bidstats:"))

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
    application.add_handler(CallbackQueryHandler(handle_bid_force_callback, pattern="^bid_force:"))

    # Custom emoji ID extractor (must be last — catches all text messages with entities)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_emoji_extract))

    # Global error handler
    application.add_error_handler(error_handler)

    logger.debug("Telegram handlers registered")
