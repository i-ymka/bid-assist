#!/usr/bin/env python3
"""
Unified entry point for Bid-Assist.

Run with: python run.py
Stop with: Ctrl+C

This single process handles:
- Freelancer API polling for new projects
- AI analysis with Gemini CLI
- Telegram bot for notifications and bid placement
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

# Parse --env before any src imports so Settings picks up the right file
_arg_parser = argparse.ArgumentParser(add_help=False)
_arg_parser.add_argument("--env", default=None)
_known, _ = _arg_parser.parse_known_args()

if _known.env is None:
    # No --env given → launch both accounts as subprocesses
    import subprocess
    import threading
    import pyfiglet
    from rich.console import Console as _RootConsole
    from rich.text import Text as _RootText
    from rich.align import Align as _RootAlign
    from rich.rule import Rule as _RootRule

    _ACCOUNTS = {"yehia": ".env.yehia", "ymka": ".env.ymka"}

    # Print banner once from parent
    _rc = _RootConsole(force_terminal=True)
    _GRAD = ["#5B2FD4","#6B3FE4","#7B4FF4","#8B5FFF","#9B70FF","#AB80FF","#BB90FF","#CCAAFF"]
    _rc.print()
    for _art_word, _offset in [("BID", 0), ("ASSIST", 3)]:
        for _i, _line in enumerate(pyfiglet.figlet_format(_art_word, font="larry3d").rstrip("\n").split("\n")):
            _t = _RootText(_line)
            _t.stylize(f"bold {_GRAD[(_i + _offset) % len(_GRAD)]}")
            _rc.print(_RootAlign(_t, align="center"))
    _rc.print()
    _rc.print(_RootRule(style="#7B4FF4"))
    _rc.print(f"  [dim]accounts[/dim] [bold #AB80FF]{' · '.join(_ACCOUNTS.keys())}[/bold #AB80FF]", justify="center")
    _rc.print(_RootRule(style="#7B4FF4"))
    _rc.print()

    import re as _re
    _ANSI_RE = _re.compile(r"\x1b\[[0-9;]*[mK]|\x1b\][^\x07]*\x07|\r")

    def _stream(proc, prefix):
        for line in iter(proc.stdout.readline, b""):
            decoded = line.decode(errors="replace").rstrip()
            if _ANSI_RE.sub("", decoded).strip():  # skip ANSI-only / blank lines
                sys.stdout.write(f"[{prefix}]{' ' * (5 - len(prefix))} {decoded}\n")
                sys.stdout.flush()

    _procs = {
        name: subprocess.Popen(
            [sys.executable, __file__, "--env", env_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for name, env_file in _ACCOUNTS.items()
    }

    for _name, _proc in _procs.items():
        threading.Thread(target=_stream, args=(_proc, _name), daemon=True).start()

    try:
        for _proc in _procs.values():
            _proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down all accounts...")
        for _proc in _procs.values():
            _proc.terminate()
        for _proc in _procs.values():
            _proc.wait()

    sys.exit(0)

os.environ["ENV_FILE"] = _known.env

from telegram import BotCommand
from telegram.ext import Application
from telegram.error import NetworkError, TelegramError

from src.config import settings
from src.services.freelancer import FreelancerClient, ProjectService, BiddingService
from src.services.storage import ProjectRepository
from src.services.telegram.handlers import setup_handlers
from src.services.telegram.notifier import Notifier
from src.services.ai.gemini_analyzer import analyze_project, analyze_feasibility, consume_exhaustion_flag
from src.services.storage.shared_repository import SharedAnalysisRepository
from src.models import AIAnalysis
from src.models.bid import Bid, Verdict
from src.filters import CountryFilter, BudgetFilter, BlacklistFilter

# Configure logging: Rich for console (INFO+), plain text for file (DEBUG+)
import pyfiglet
from rich.logging import RichHandler
from rich.console import Console
from rich.theme import Theme
from rich.text import Text
from rich.align import Align
from rich.rule import Rule

_console_theme = Theme({
    "logging.level.info":    "bold cyan",
    "logging.level.warning": "bold yellow",
    "logging.level.error":   "bold red",
    "logging.level.critical":"bold white on red",
    "logging.level.debug":   "dim white",
})
_console = Console(theme=_console_theme, force_terminal=True)

class _LevelPrefix(logging.Filter):
    """Inject colored level tag into messages (since show_level=False).
    WARN/ERR/CRIT get colored tags. INFO gets blank padding unless it already
    starts with a status word like PASS/SKIP/BID►/LIVE (those carry their own color)."""
    _TAGS = {
        logging.WARNING:  "[bold yellow]WARN[/bold yellow]  ",
        logging.ERROR:    "[bold red]ERR! [/bold red] ",
        logging.CRITICAL: "[bold white on red]CRIT[/bold white on red] ",
    }
    _BLANK = "      "  # 6 spaces — same width as "WARN  "
    def filter(self, record):
        tag = self._TAGS.get(record.levelno)
        if tag:
            try:
                record.msg = tag + record.getMessage()
            except Exception:
                record.msg = tag + str(record.msg)
            record.args = ()
        elif record.levelno == logging.INFO:
            msg = str(record.msg)
            if not msg.startswith("[bold"):
                try:
                    record.msg = self._BLANK + record.getMessage()
                except Exception:
                    record.msg = self._BLANK + msg
                record.args = ()
        return True

_rich_handler = RichHandler(
    console=_console,
    show_path=False,
    show_level=False,
    omit_repeated_times=True,
    rich_tracebacks=True,
    tracebacks_show_locals=False,
    markup=True,
    log_time_format="[%H:%M:%S]",
)
_rich_handler.addFilter(_LevelPrefix())
_rich_handler.setLevel(logging.INFO)

_file_handler = logging.FileHandler("logs/bot_debug.log")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[_rich_handler, _file_handler],
    format="%(message)s",
    datefmt="[%H:%M:%S]",
)
logger = logging.getLogger(__name__)

# Silence noisy third-party loggers
for _noisy in ("httpx", "httpcore", "telegram", "telegram.ext.Updater", "hpack", "asyncio",
               "apscheduler", "apscheduler.scheduler", "apscheduler.executors.default"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)
logging.getLogger("apscheduler").setLevel(logging.CRITICAL)

# Global flag for graceful shutdown
shutdown_event = asyncio.Event()


def _print_banner(account: str, model: str, bid_model: str, pool_count: int) -> None:
    """Print a startup banner with gradient ASCII art."""
    _GRAD = ["#5B2FD4", "#6B3FE4", "#7B4FF4", "#8B5FFF", "#9B70FF", "#AB80FF", "#BB90FF", "#CCAAFF"]

    def _gradient_line(line: str, offset: int = 0) -> Text:
        t = Text(line)
        t.stylize(f"bold {_GRAD[(offset) % len(_GRAD)]}")
        return t

    bid_art   = pyfiglet.figlet_format("BID",    font="larry3d").rstrip("\n").split("\n")
    assist_art = pyfiglet.figlet_format("ASSIST", font="larry3d").rstrip("\n").split("\n")

    _console.print()
    for i, line in enumerate(bid_art):
        _console.print(Align(_gradient_line(line, i), align="center"))
    for i, line in enumerate(assist_art):
        _console.print(Align(_gradient_line(line, i + 3), align="center"))
    _console.print()
    _console.print(Rule(style="#7B4FF4"))
    _console.print(
        f"  [dim]account[/dim] [bold #AB80FF]{account}[/bold #AB80FF]"
        f"    [dim]analyze[/dim] [bold #AB80FF]{model}[/bold #AB80FF]"
        f"    [dim]bid[/dim] [bold #AB80FF]{bid_model}[/bold #AB80FF]"
        f"    [dim]pool[/dim] [bold #AB80FF]{pool_count} accounts[/bold #AB80FF]",
        justify="center",
    )
    _console.print(Rule(style="#7B4FF4"))
    _console.print()


async def polling_loop(repo: ProjectRepository, project_service: ProjectService, bidding_service: BiddingService, shared_repo: SharedAnalysisRepository):
    """Background task that polls Freelancer API for new projects."""
    logger.debug("Polling loop started")

    while not shutdown_event.is_set():
        try:
            # Check if paused
            if repo.is_paused():
                await asyncio.sleep(5)
                continue

            interval = repo.get_poll_interval()
            logger.debug(f"--- Polling cycle (interval: {interval}s) ---")

            # Use skill_ids from .env
            skill_ids = settings.skill_ids

            # Fetch projects already bid on from Freelancer API
            already_bid_ids = bidding_service.get_my_bidded_project_ids(limit=200)

            # Fetch projects
            projects = project_service.get_active_projects(
                skill_ids=skill_ids,
                min_budget=50,  # Default: $50 (actual filter in BudgetFilter uses runtime settings)
            )

            # Initialize filters (budget range is read from DB each cycle — user can change it live)
            budget_min, budget_max = repo.get_budget_range()
            logger.debug(f"Budget filter: ${budget_min}-${budget_max}")
            budget_filter = BudgetFilter(min_budget=budget_min, max_budget=budget_max)
            blacklist_filter = BlacklistFilter()
            country_filter = CountryFilter()

            new_count = 0
            filtered_count = 0
            already_bid_count = 0
            for project in projects:
                # Skip if already processed or in queue
                if repo.is_processed(project.id) or repo.is_in_queue(project.id):
                    continue

                # If another account already decided SKIP — mark processed, don't queue
                if shared_repo.is_claimed(project.id):
                    cached = shared_repo.get_result(project.id)
                    if cached and cached.get("verdict") == "SKIP":
                        repo.add_processed_project(project.id)
                        continue
                    # BID verdict or in_progress — each account queues and handles independently

                # Skip if already bid on (from Freelancer API)
                if project.id in already_bid_ids:
                    logger.debug(f"SKIPPED {project.id}: already bid on")
                    repo.add_processed_project(project.id)
                    already_bid_count += 1
                    continue

                # Apply budget filter (no API call needed)
                if not budget_filter.passes(project):
                    reason = budget_filter.get_rejection_reason(project)
                    logger.debug(f"FILTERED {project.id}: {reason}")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply currency filter
                if settings.blocked_currencies:
                    project_currency = project.currency.code.upper()
                    if project_currency in settings.blocked_currencies:
                        logger.debug(f"FILTERED {project.id}: currency {project_currency} blocked")
                        repo.add_processed_project(project.id)
                        filtered_count += 1
                        continue

                # Apply language filter (check before AI analysis)
                if settings.allowed_languages:
                    project_lang = project.language.lower()
                    if project_lang not in settings.allowed_languages:
                        logger.debug(f"FILTERED {project.id}: language {project_lang}")
                        repo.add_processed_project(project.id)
                        filtered_count += 1
                        continue

                # Apply max bid count filter (early gate to avoid wasting AI calls)
                max_bids = repo.get_max_bid_count()
                if project.bid_stats.bid_count > max_bids:
                    logger.debug(f"FILTERED {project.id}: {project.bid_stats.bid_count} bids > limit {max_bids}")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply project age filter
                if project.time_submitted:
                    age_hours = (datetime.utcnow() - project.time_submitted).total_seconds() / 3600
                    if age_hours > settings.max_project_age_hours:
                        logger.debug(f"FILTERED {project.id}: too old ({age_hours:.1f}h)")
                        repo.add_processed_project(project.id)
                        filtered_count += 1
                        continue

                # Apply preferred-only filter
                if repo.skip_preferred_only() and project.is_preferred_only:
                    logger.debug(f"FILTERED {project.id}: preferred-only")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply blacklist filter
                if not blacklist_filter.passes(project):
                    reason = blacklist_filter.get_rejection_reason(project)
                    logger.debug(f"FILTERED {project.id}: {reason}")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply verification filter (skip verification-required projects)
                if not repo.is_verified() and settings.verification_keywords:
                    text_to_check = f"{project.title} {project.description}".lower()
                    skill_names = " ".join([job.name.lower() for job in project.jobs])
                    text_to_check += f" {skill_names}"

                    requires_verification = False
                    for keyword in settings.verification_keywords:
                        if keyword in text_to_check:
                            logger.debug(f"FILTERED {project.id}: requires verified account ('{keyword}')")
                            repo.add_processed_project(project.id)
                            filtered_count += 1
                            requires_verification = True
                            break
                    if requires_verification:
                        continue

                # Fetch owner country
                if not project.owner.country or project.owner.country == "Unknown":
                    owner_country = project_service.get_project_owner_country(project.id)
                    if owner_country:
                        project.owner.country = owner_country
                    else:
                        project.owner.country = "Unknown"

                # Apply country filter
                if not country_filter.passes(project):
                    reason = country_filter.get_rejection_reason(project)
                    logger.debug(f"FILTERED {project.id}: {reason}")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Extract skill names for keyword matching
                skill_names = ",".join([job.name for job in project.jobs])

                # Add to queue for analysis
                repo.add_to_queue(
                    project_id=project.id,
                    title=project.title,
                    description=project.description,
                    budget_min=project.budget.minimum,
                    budget_max=project.budget.maximum,
                    currency=project.currency.code,
                    client_country=project.owner.country,
                    bid_count=project.bid_stats.bid_count,
                    avg_bid=project.bid_stats.bid_avg,
                    url=project.url,
                    time_submitted=project.time_submitted,
                    skill_names=skill_names,
                    owner_username=project.owner.username,
                    owner_display_name=project.owner.display_name or "",
                    is_preferred_only=project.is_preferred_only,
                )
                logger.info(f"[cyan]  {project.title[:60]}  [{project.owner.country}][/cyan]")
                new_count += 1

            pending = repo.get_queue_count("pending")
            if new_count > 0:
                logger.info(f"Polling: +{new_count} queued, {pending} pending")
            else:
                logger.debug(f"Polling: 0 new, {pending} pending")

            # Save poll stats for /status command
            total_found = new_count + filtered_count + already_bid_count
            repo.set_last_poll_stats(
                found=total_found,
                filtered=filtered_count,
                queued=new_count,
                already_bid=already_bid_count,
            )

            # Wait for next cycle
            for _ in range(interval):
                if shutdown_event.is_set():
                    break
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Polling error: {e}")
            await asyncio.sleep(30)


def _recheck_queue_filters(project_data: dict, repo: ProjectRepository) -> "Optional[str]":
    """Re-check all polling-time filters against current settings on queue exit.

    Returns a skip reason string if the project should now be filtered out,
    or None if it still passes all filters. Uses only stored queue data —
    no API calls. A separate fresh-API bid_count check is done right before bid placement.
    """
    from typing import Optional

    project_id = project_data["project_id"]

    # Age filter — most common reason for stale projects in queue
    time_submitted = project_data.get("time_submitted")
    if time_submitted:
        if isinstance(time_submitted, str):
            try:
                time_submitted = datetime.strptime(time_submitted, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                try:
                    time_submitted = datetime.fromisoformat(time_submitted)
                except ValueError:
                    time_submitted = None
        if time_submitted:
            age_hours = (datetime.utcnow() - time_submitted).total_seconds() / 3600
            if age_hours > settings.max_project_age_hours:
                return f"Too old ({age_hours:.1f}h > {settings.max_project_age_hours}h)"

    # Budget filter — user may have changed budget range
    budget_max = project_data.get("budget_max", 0)
    if budget_max:
        budget_min_setting, budget_max_setting = repo.get_budget_range()
        if not (budget_min_setting <= budget_max <= budget_max_setting):
            return f"Budget {budget_max:.0f} outside current range {budget_min_setting}-{budget_max_setting}"

    # Currency filter
    currency = (project_data.get("currency") or "USD").upper()
    if settings.blocked_currencies and currency in settings.blocked_currencies:
        return f"Currency '{currency}' is blocked"

    # Blacklist filter
    if settings.blacklist_keywords:
        text = f"{project_data.get('title', '')} {project_data.get('description', '')}".lower()
        for kw in settings.blacklist_keywords:
            if kw.lower() in text:
                return f"Blacklisted keyword: '{kw}'"

    # Verification filter
    if not repo.is_verified() and settings.verification_keywords:
        skills = project_data.get("skill_names") or ""
        text = f"{project_data.get('title', '')} {project_data.get('description', '')} {skills}".lower()
        for kw in settings.verification_keywords:
            if kw in text:
                return f"Requires verified account (keyword: '{kw}')"

    # Country filter (inline — same logic as CountryFilter, no Project object needed)
    client_country = (project_data.get("client_country") or "").lower().strip()
    block_unknown = getattr(settings, "block_unknown_countries", False)
    if not client_country or client_country == "unknown":
        if block_unknown:
            return "Country is unknown (blocked by settings)"
    elif settings.allowed_countries:
        if client_country not in settings.allowed_countries:
            return f"Country '{client_country}' not in allowed list"
    elif settings.blocked_countries and client_country in settings.blocked_countries:
        return f"Country '{client_country}' is blocked"

    # Preferred-only filter
    if repo.skip_preferred_only() and project_data.get("is_preferred_only"):
        return "Preferred freelancer only"

    # bid_count setting change (stale data, but catches when user lowered the limit)
    bid_count = project_data.get("bid_count", 0)
    max_bids = repo.get_max_bid_count()
    if bid_count > max_bids:
        return f"Too many bids ({bid_count} > {max_bids}) — limit changed after queued"

    return None


async def analysis_loop(repo: ProjectRepository, notifier: Notifier, shared_repo: SharedAnalysisRepository, project_service: ProjectService):
    """Background task that analyzes projects with Gemini AI."""
    logger.debug("Analysis loop started")

    while not shutdown_event.is_set():
        project_id = None  # Reset each iteration so except block can check if a project was in-flight
        try:
            # Check if paused
            if repo.is_paused():
                await asyncio.sleep(5)
                continue

            # Get next project from queue
            project_data = repo.get_next_from_queue()
            if not project_data:
                await asyncio.sleep(5)
                continue

            project_id = project_data["project_id"]

            # Re-check all filters against current settings before spending AI tokens.
            # Catches: aged-out projects, budget/blacklist/country/verified/preferred changes.
            skip_reason = _recheck_queue_filters(project_data, repo)
            if skip_reason:
                logger.info(f"[bold yellow]NOPE[/bold yellow]  {project_data['title'][:55]}  ({skip_reason})")
                repo.remove_from_queue(project_id)
                repo.add_processed_project(project_id)
                continue

            # Format budget string
            budget_min = project_data.get("budget_min", 0)
            budget_max = project_data.get("budget_max", 0)
            currency = project_data.get("currency", "USD")
            avg_bid = project_data.get("avg_bid", 0)
            bid_count = project_data.get("bid_count", 0)

            # Convert to USD for AI analysis
            from src.services.currency import to_usd, from_usd, round_up_10
            budget_min_usd = to_usd(budget_min, currency) if budget_min else 0
            budget_max_usd = to_usd(budget_max, currency) if budget_max else 0
            avg_bid_usd = to_usd(avg_bid, currency) if avg_bid else 0

            if budget_min_usd and budget_max_usd:
                budget_str = f"{budget_min_usd:.0f} - {budget_max_usd:.0f} USD"
            elif budget_max_usd:
                budget_str = f"up to {budget_max_usd:.0f} USD"
            else:
                budget_str = "Not specified"

            # Pre-filter: estimate our bid price before spending Gemini tokens.
            # If avg_bid * bid_adjustment is already below min_daily_rate, skip immediately.
            _min_daily_rate = repo.get_min_daily_rate()
            _bid_adjustment = repo.get_bid_adjustment()
            _raw = avg_bid_usd if avg_bid_usd > 0 else (budget_min_usd + budget_max_usd) / 2
            if _raw > 0:
                _target_est = _raw * (1 + _bid_adjustment / 100)
                if _target_est < _min_daily_rate:
                    logger.info(f"[bold yellow]NOPE[/bold yellow]  {project_data['title'][:55]}  (${_target_est:.0f} < ${_min_daily_rate}/d)")
                    repo.remove_from_queue(project_id)
                    repo.add_processed_project(project_id)
                    continue

            # --- Shared analysis cache: avoid duplicate Call 1 across accounts ---
            loop = asyncio.get_event_loop()
            cached_feasibility = shared_repo.get_result(project_id)

            if cached_feasibility is None:
                # No cached result — try to claim the Call 1 slot
                claimed = shared_repo.try_claim(project_id)
                if not claimed:
                    # Another account is running Call 1 — wait for their result (up to 2 min)
                    logger.debug(f"Project {project_id}: waiting for Call 1 from another account...")
                    deadline = loop.time() + 120
                    while loop.time() < deadline:
                        await asyncio.sleep(5)
                        cached_feasibility = shared_repo.get_result(project_id)
                        if cached_feasibility is not None:
                            break
                    if cached_feasibility is None:
                        logger.debug(f"Project {project_id}: no Call 1 result after 2 min, skipping")
                        repo.remove_from_queue(project_id)
                        repo.add_processed_project(project_id)
                        continue

                # We own the slot — run Call 1 exclusively
                logger.info(f"[white]  ▸ {project_data['title'][:60]}[/white]")
                repo.mark_queue_status(project_id, "analyzing")
                try:
                    raw_feasibility = await loop.run_in_executor(
                        None, analyze_feasibility,
                        project_id, project_data["title"], project_data["description"],
                        budget_str, avg_bid_usd, bid_count,
                    )
                except Exception:
                    shared_repo.release_claim(project_id)
                    raise
                if raw_feasibility:
                    shared_repo.store_result(
                        project_id,
                        raw_feasibility["verdict"],
                        raw_feasibility.get("days", 1),
                        raw_feasibility.get("summary", ""),
                    )
                    cached_feasibility = raw_feasibility
                else:
                    # Call 1 failed — release slot so other accounts can retry later, skip Call 2
                    shared_repo.release_claim(project_id)
                    continue
            else:
                logger.debug(f"Project {project_id}: using cached Call 1 result (verdict={cached_feasibility['verdict']})")
                # If another account already decided SKIP, mark processed locally so we don't re-queue
                if cached_feasibility.get("verdict") == "SKIP":
                    repo.remove_from_queue(project_id)
                    repo.add_processed_project(project_id)
                    continue
                repo.mark_queue_status(project_id, "analyzing")

            owner_name = ""  # Freelancer API does not expose owner display name to freelancers

            # Run Gemini analysis (Call 2 always per-account; Call 1 skipped if cached)
            min_daily_rate = repo.get_min_daily_rate()
            bid_adjustment = repo.get_bid_adjustment()
            result = await loop.run_in_executor(
                None,
                partial(
                    analyze_project,
                    project_id,
                    project_data["title"],
                    project_data["description"],
                    budget_str,
                    avg_bid_usd,
                    bid_count,
                    budget_min_usd,
                    budget_max_usd,
                    min_daily_rate,
                    owner_name,
                    bid_adjustment,
                    cached_feasibility,  # None → analyze_project runs Call 1 itself (fallback)
                ),
            )

            if not result:
                logger.error(f"FAILED  {project_data['title'][:55]}")
                # Check if all Gemini accounts hit quota — notify user once and pause 30 min
                if consume_exhaustion_flag():
                    logger.warning("All Gemini accounts exhausted — sending notification, pausing 30 min")
                    await notifier.send_quota_exhausted_notification()
                    # Reset project to pending so we retry it after the sleep — do NOT re-queue from poll
                    repo.mark_queue_status(project_id, "pending")
                    shared_repo.release_claim(project_id)
                    await asyncio.sleep(1800)
                else:
                    # Transient failure — remove so next poll cycle can re-queue and retry
                    repo.remove_from_queue(project_id)
                continue

            # Convert code-calculated USD amount to project currency
            if currency != "USD" and result.verdict == "BID" and result.amount > 0:
                original_usd = result.amount
                result.amount = round_up_10(from_usd(result.amount, currency))
                logger.debug(f"Amount conversion: {original_usd:.0f} USD → {result.amount} {currency}")

            # Mark as processed
            repo.mark_queue_status(project_id, "processed")
            repo.add_processed_project(project_id)

            # Send notification based on verdict
            if result.verdict == "BID":
                # Check auto-bid mode
                if repo.is_auto_bid():
                    # Last-mile competitor check: fresh bid_count from API right before placing
                    max_bids_now = repo.get_max_bid_count()
                    fresh_project = await loop.run_in_executor(
                        None, project_service.get_project_details, project_id
                    )
                    if not fresh_project:
                        logger.info(f"AUTO-BID SKIPPED project {project_id}: project no longer available (deleted or closed)")
                        continue
                    fresh_bid_count = fresh_project.bid_stats.bid_count
                    if fresh_bid_count > max_bids_now:
                        logger.info(f"AUTO-BID SKIPPED project {project_id}: {fresh_bid_count} bids now > limit {max_bids_now}")
                        continue

                    # Auto-bid: place bid immediately
                    logger.info(f"AUTO-BID: Placing bid on {project_id} - ${result.amount}")
                    bidding_service = BiddingService()
                    bid = Bid(
                        project_id=project_id,
                        amount=result.amount,
                        period=result.period,
                        milestone_percentage=settings.default_milestone_pct,
                        description=result.bid_text,
                    )
                    bid_result = await asyncio.get_event_loop().run_in_executor(
                        None, bidding_service.place_bid, bid
                    )

                    # Record in bid history
                    repo.add_bid_record(
                        project_id=project_id,
                        amount=result.amount,
                        period=result.period,
                        description=result.bid_text,
                        success=bid_result.success,
                        error_message=bid_result.message if not bid_result.success else None,
                        title=project_data["title"],
                        summary=result.summary,
                        url=project_data.get("url", ""),
                        currency=currency,
                        bid_count=project_data.get("bid_count", 0),
                        budget_min=budget_min,
                        budget_max=budget_max,
                        client_country=project_data.get("client_country", ""),
                        avg_bid=project_data.get("avg_bid", 0),
                    )

                    # Get rank info and remaining bids right after placing bid
                    rank_info = None
                    remaining_bids = None
                    if bid_result.success and bid_result.bid_id:
                        try:
                            rank_info = await asyncio.get_event_loop().run_in_executor(
                                None, bidding_service.get_bid_rank,
                                bid_result.bid_id, project_id, 1.0,
                            )
                        except Exception:
                            pass
                        try:
                            remaining_bids = await asyncio.get_event_loop().run_in_executor(
                                None, bidding_service.get_remaining_bids,
                            )
                        except Exception:
                            pass

                    # Send auto-bid result notification to all chat_ids
                    notif_sent = False
                    for chat_id in settings.telegram_chat_ids:
                        if bid_result.success:
                            msg, orig_text, orig_keyboard = await notifier.send_auto_bid_notification(
                                chat_id=chat_id,
                                project_id=project_id,
                                title=project_data["title"],
                                budget_min=budget_min,
                                budget_max=budget_max,
                                currency=currency,
                                client_country=project_data.get("client_country", "Unknown"),
                                bid_count=project_data.get("bid_count", 0),
                                avg_bid=project_data.get("avg_bid", 0),
                                url=project_data.get("url", ""),
                                summary=result.summary,
                                bid_text=result.bid_text,
                                amount=result.amount,
                                period=result.period,
                                bid_id=bid_result.bid_id,
                                rank_info=rank_info,
                                remaining_bids=remaining_bids,
                                fair_price=result.fair_price,
                            )
                            if msg:
                                notif_sent = True

                            # Schedule delayed update with fresh stats
                            if msg and bid_result.bid_id:
                                from src.services.telegram.notifier import schedule_price_corrections
                                asyncio.create_task(
                                    schedule_price_corrections(
                                        bot=notifier._bot,
                                        chat_id=chat_id,
                                        message_id=msg.message_id,
                                        project_id=project_id,
                                        bid_id=bid_result.bid_id,
                                        bidding_service=bidding_service,
                                        currency=currency,
                                        original_amount=result.amount,
                                        days=result.period,
                                        min_daily_rate=repo.get_min_daily_rate(),
                                        original_text=orig_text,
                                        original_keyboard=orig_keyboard,
                                    )
                                )

                        else:
                            # Check if error is about bid limit
                            error_lower = bid_result.message.lower()
                            if "preferred freelancer" in error_lower:
                                # Project became preferred-only after being queued — silent skip
                                logger.info(f"[bold yellow]NOPE[/bold yellow]  {project_data['title'][:55]}  (preferred-only)")
                            elif "used all" in error_lower or "all of your bids" in error_lower or ("bid" in error_lower and ("limit" in error_lower or "remain" in error_lower or "run out" in error_lower)):
                                # Disable auto-bid
                                repo.set_auto_bid(False)
                                logger.warning("AUTO-BID DISABLED: No bids remaining")
                                for cid in settings.telegram_chat_ids:
                                    await notifier.send_to_user(
                                        cid,
                                        "⚠️ *Auto\\-bid disabled* — no bids remaining\\. Projects will continue in manual mode\\.",
                                    )
                            else:
                                await notifier.send_auto_bid_failed_notification(
                                    chat_id=chat_id,
                                    project_id=project_id,
                                    title=project_data["title"],
                                    url=project_data.get("url", ""),
                                    amount=result.amount,
                                    error=bid_result.message,
                                )
                                logger.error(f"FAIL  {project_data['title'][:55]}  — {bid_result.message}")

                    # Mark notification as sent in bid_history
                    if notif_sent:
                        repo.mark_notification_sent(project_id)
                        logger.info(f"[bold green]SENT[/bold green]  {project_data['title'][:55]}  ${result.amount}  ({result.period}d)")
                else:
                    # Manual mode: store pending bid and send notification with Place Bid button
                    repo.add_pending_bid(
                        project_id=project_id,
                        amount=result.amount,
                        period=result.period,
                        description=result.bid_text,
                        title=project_data["title"],
                        currency=currency,
                        url=project_data.get("url", ""),
                        bid_count=project_data.get("bid_count", 0),
                        summary=result.summary,
                        budget_min=budget_min,
                        budget_max=budget_max,
                        client_country=project_data.get("client_country", ""),
                        avg_bid=avg_bid,
                    )

                    # Store in bid_history too (not yet placed, but notification data preserved)
                    repo.add_bid_record(
                        project_id=project_id,
                        amount=result.amount,
                        period=result.period,
                        description=result.bid_text,
                        success=False,
                        error_message="pending_manual",
                        title=project_data["title"],
                        summary=result.summary,
                        url=project_data.get("url", ""),
                        currency=currency,
                        bid_count=project_data.get("bid_count", 0),
                        budget_min=budget_min,
                        budget_max=budget_max,
                        client_country=project_data.get("client_country", ""),
                        avg_bid=avg_bid,
                    )

                    # BiddingService for delayed stats update
                    bid_svc = BiddingService()

                    for chat_id in settings.telegram_chat_ids:
                        msg, orig_text, orig_keyboard = await notifier.send_gpt_decision_notification_to_user(
                            chat_id=chat_id,
                            project_id=project_id,
                            title=project_data["title"],
                            budget_min=budget_min,
                            budget_max=budget_max,
                            currency=currency,
                            client_country=project_data.get("client_country", "Unknown"),
                            bid_count=project_data.get("bid_count", 0),
                            avg_bid=project_data.get("avg_bid", 0),
                            url=project_data.get("url", ""),
                            summary=result.summary,
                            bid_text=result.bid_text,
                            suggested_amount=result.amount,
                            suggested_period=result.period,
                        )
                        if msg:
                            repo.mark_notification_sent(project_id)
                            # Schedule delayed bids line update (60s)
                            from src.services.telegram.notifier import schedule_bid_update
                            asyncio.create_task(
                                schedule_bid_update(
                                    bot=notifier._bot,
                                    chat_id=chat_id,
                                    message_id=msg.message_id,
                                    project_id=project_id,
                                    bidding_service=bid_svc,
                                    currency=currency,
                                    original_text=orig_text,
                                    original_keyboard=orig_keyboard,
                                )
                            )
                        logger.info(f"[bold bright_magenta]BID[/bold bright_magenta]  {project_data['title'][:55]}  ${result.amount}  ({result.period}d)")
            else:
                # SKIP verdict — send notification if receive_skipped is enabled
                if repo.get_receive_skipped():
                    for chat_id in settings.telegram_chat_ids:
                        await notifier.send_skip_notification_to_user(
                            chat_id=chat_id,
                            project_id=project_id,
                            title=project_data["title"],
                            budget_min=budget_min,
                            budget_max=budget_max,
                            currency=currency,
                            client_country=project_data.get("client_country", "Unknown"),
                            url=project_data.get("url", ""),
                            summary=result.summary,
                        )
                    logger.info(f"[bold red]SKIP[/bold red]  {project_data['title'][:55]}")
                else:
                    logger.debug(f"SKIP (muted)  {project_data['title'][:55]}")

        except Exception as e:
            logger.error(f"Analysis error: {e}", exc_info=True)
            if project_id:
                repo.remove_from_queue(project_id)
                repo.add_processed_project(project_id)
            await asyncio.sleep(10)


async def cleanup_loop(repo: ProjectRepository, shared_repo: SharedAnalysisRepository):
    """Background task that cleans up old data."""
    logger.debug("Cleanup loop started")

    while not shutdown_event.is_set():
        try:
            # Clean up every hour
            await asyncio.sleep(3600)

            removed = repo.cleanup_old_queue_items(max_age_hours=24)
            if removed > 0:
                logger.info(f"Cleaned up {removed} old projects from queue")

            shared_repo.cleanup_stale(max_age_hours=24)

        except Exception as e:
            logger.error(f"Cleanup error: {e}")


async def main():
    """Main entry point."""

    # Initialize services
    repo = ProjectRepository()
    shared_repo = SharedAnalysisRepository(Path(settings.db_path).parent / "shared_analysis.db")
    shared_repo.release_stale_claims()  # clean up any in_progress claims left from a previous crashed run
    client = FreelancerClient()
    project_service = ProjectService(client)
    bidding_service = BiddingService(client)
    notifier = Notifier()

    # Reset processed projects if testing mode is enabled
    if settings.reset_on_start:
        logger.warning("⚠️  RESET_ON_START=true - Clearing all processed projects!")
        result = repo.reset_for_testing()
        logger.warning(f"   Cleared: {result.get('processed_cleared', 0)} processed, "
                      f"{result.get('queue_cleared', 0)} queue, "
                      f"{result.get('pending_cleared', 0)} pending")

    # Record bot start time
    repo.set_bot_start_time()
    logger.info(f"Bot started — account: {settings.username} | model: {settings.gemini_model} → {settings.bid_model}")

    # Build Telegram application
    try:
        app = Application.builder().token(settings.telegram_bot_token).build()
        setup_handlers(app)

        # Initialize the application
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
    except NetworkError as e:
        logger.error("=" * 50)
        logger.error("❌ Cannot connect to Telegram API")
        logger.error(f"   Error: {str(e)}")
        logger.error("")
        logger.error("   Possible solutions:")
        logger.error("   1. Check your internet connection")
        logger.error("   2. If using VPN/proxy - make sure it's working")
        logger.error("   3. Try again in a few seconds")
        logger.error("   4. Check if Telegram is blocked in your region")
        logger.error("=" * 50)
        raise
    except TelegramError as e:
        logger.error("=" * 50)
        logger.error("❌ Invalid Telegram bot token")
        logger.error(f"   Error: {str(e)}")
        logger.error("")
        logger.error("   Solution:")
        logger.error("   Check TELEGRAM_BOT_TOKEN in your .env file")
        logger.error("   Get a new token from @BotFather if needed")
        logger.error("=" * 50)
        raise

    # Set bot commands menu (visible in "/" menu)
    await app.bot.set_my_commands([
        BotCommand("status", "Status & Control"),
        BotCommand("settings", "Bot settings"),
        BotCommand("bidstats", "Bid history"),
        BotCommand("help", "Help"),
    ])

    logger.debug("Telegram bot started")
    logger.debug("Press Ctrl+C to stop")

    # Start background tasks
    tasks = [
        asyncio.create_task(polling_loop(repo, project_service, bidding_service, shared_repo)),
        asyncio.create_task(analysis_loop(repo, notifier, shared_repo, project_service)),
        asyncio.create_task(cleanup_loop(repo, shared_repo)),
    ]

    # Wait for shutdown signal
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    # Cleanup
    logger.info("Shutting down...")

    # Cancel background tasks
    for task in tasks:
        task.cancel()

    await asyncio.gather(*tasks, return_exceptions=True)

    # Stop Telegram bot
    await app.updater.stop()
    await app.stop()
    await app.shutdown()

    repo.close()
    logger.info("Shutdown complete")


def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    logger.info("Received shutdown signal...")
    shutdown_event.set()


if __name__ == "__main__":
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Run the main loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except NetworkError as e:
        logger.error("=" * 50)
        logger.error("❌ Network error - cannot connect to Telegram")
        logger.error(f"   {str(e)}")
        logger.error("   Possible reasons:")
        logger.error("   - No internet connection")
        logger.error("   - VPN/proxy issues")
        logger.error("   - DNS resolution problems")
        logger.error("   - Telegram API is blocked in your region")
        logger.error("=" * 50)
        sys.exit(1)
    except TelegramError as e:
        logger.error("=" * 50)
        logger.error("❌ Telegram API error")
        logger.error(f"   {str(e)}")
        logger.error("   Check your TELEGRAM_BOT_TOKEN in .env file")
        logger.error("=" * 50)
        sys.exit(1)
    except Exception as e:
        logger.error("=" * 50)
        logger.error(f"❌ Unexpected error: {type(e).__name__}")
        logger.error(f"   {str(e)}")
        logger.error("=" * 50)
        logger.exception("Full traceback:")
        sys.exit(1)

    sys.exit(0)
