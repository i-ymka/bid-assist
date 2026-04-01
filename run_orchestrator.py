#!/usr/bin/env python3
"""Unified orchestrator entry point.

Single process that manages all accounts: one polling loop,
parallel Call 1, per-account Call 2 and bidding.

Usage:
    python run_orchestrator.py                      # auto-discover all .env.* files
    python run_orchestrator.py --accounts .env.ymka  # specific accounts only
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

# ── Parse args BEFORE any imports that touch settings ──
parser = argparse.ArgumentParser(description="Bid-Assist Orchestrator")
parser.add_argument(
    "--accounts",
    nargs="*",
    help="Specific .env files to load (e.g. .env.ymka .env.yehia). Default: auto-discover.",
)
args = parser.parse_args()

# Set ENV_FILE to first account so the global `settings` singleton can load
# (needed by modules that import `from src.config import settings` at module level)
if args.accounts:
    os.environ["ENV_FILE"] = args.accounts[0]
else:
    # Auto-discover, pick first
    import glob
    env_files = sorted(f for f in glob.glob(".env.*") if not f.endswith(".example") and not f.endswith(".bak"))
    if env_files:
        os.environ["ENV_FILE"] = env_files[0]

# ── Now import everything ──
from src.config.loader import build_config
from src.services.storage.unified_repo import UnifiedRepo
from src.orchestrator.services import init_all_services
from src.orchestrator.polling import polling_loop
from src.orchestrator.analyzer import analysis_dispatcher
from src.orchestrator.bidder import bid_dispatcher
from src.orchestrator.cleanup import cleanup_loop
from src.filters.tagger import ProjectTagger

# Telegram imports
from telegram.error import NetworkError, TelegramError
from src.orchestrator.telegram import start_all_bots, stop_all_bots

# Logging — same style as run.py (colored account prefix, no Telegram spam)
import re as _re
from rich.logging import RichHandler
from rich.console import Console
from rich.theme import Theme

_console_theme = Theme({
    "logging.level.info":    "bold cyan",
    "logging.level.warning": "bold yellow",
    "logging.level.error":   "bold red",
    "logging.level.critical":"bold white on red",
    "logging.level.debug":   "dim white",
})
_console = Console(theme=_console_theme, force_terminal=True, width=200, color_system="truecolor")


class _OrchestratorPrefix(logging.Filter):
    """Prepend timestamp + level tag. Account prefix is added by each module."""
    _TAGS = {
        logging.WARNING:  "[bright_yellow]WARN[/bright_yellow]  ",
        logging.ERROR:    "[red1]ERR! [/red1] ",
        logging.CRITICAL: "[bold white on red]CRIT[/bold white on red] ",
    }
    _BLANK = "      "  # 6 spaces — same width as "WARN  "
    _STATUS_RE = _re.compile(r'^\[[^\]]+\](PASS|SKIP|NOPE|YEP|SENT|FAIL|BID)\b')

    def filter(self, record):
        from datetime import datetime
        ts = datetime.fromtimestamp(record.created).strftime('%H:%M:%S')
        prefix = f"[dim][{ts}][/dim]  "
        tag = self._TAGS.get(record.levelno)
        if tag:
            try:
                record.msg = prefix + tag + record.getMessage()
            except Exception:
                record.msg = prefix + tag + str(record.msg)
            record.args = ()
        elif record.levelno == logging.INFO:
            msg = str(record.msg)
            pad = "" if self._STATUS_RE.match(msg) else self._BLANK
            try:
                record.msg = prefix + pad + record.getMessage()
            except Exception:
                record.msg = prefix + pad + msg
            record.args = ()
        return True


class _CompactRichHandler(RichHandler):
    """RichHandler without blank lines between log entries."""
    def emit(self, record):
        # Rich adds a trailing newline via Console.print — override to use print without extra spacing
        try:
            msg = self.format(record)
            self.console.print(msg, markup=True, highlight=False)
        except Exception:
            self.handleError(record)

_rich_handler = _CompactRichHandler(
    console=_console,
    show_path=False,
    show_level=False,
    show_time=False,
    rich_tracebacks=True,
    tracebacks_show_locals=False,
    markup=True,
)
_rich_handler.addFilter(_OrchestratorPrefix())
_rich_handler.setLevel(logging.INFO)

_file_handler = logging.FileHandler("logs/bot_debug.log")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))

# Clear any existing handlers first, then set ours
logging.root.handlers.clear()
logging.root.addHandler(_rich_handler)
logging.root.addHandler(_file_handler)
logging.root.setLevel(logging.DEBUG)

# Silence noisy third-party loggers
for _noisy in ("httpx", "httpcore", "hpack", "asyncio",
               "apscheduler", "apscheduler.scheduler", "apscheduler.executors.default",
               "urllib3", "urllib3.connectionpool"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
for _tg in ("telegram", "telegram.ext", "telegram.ext.Updater",
            "telegram._bot", "telegram.ext._application", "telegram.ext.ApplicationBuilder"):
    logging.getLogger(_tg).setLevel(logging.CRITICAL)
logging.getLogger("apscheduler").setLevel(logging.CRITICAL)
# httpx is the HTTP client used by python-telegram-bot v20+
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Shutdown: threading.Event (thread-safe, works from signal handlers)
import threading
_shutdown = threading.Event()


def _sigint_handler(*args):
    """Signal handler — works with both signal.signal(sig, frame) and loop.add_signal_handler()."""
    from src.services.ai.gemini_analyzer import shutdown_gemini
    if _shutdown.is_set():
        logger.info("Force shutdown!")
        shutdown_gemini()
        os._exit(1)
    logger.info("Graceful shutdown — finishing active projects... (Ctrl+C again to force)")
    shutdown_gemini()
    _shutdown.set()


def setup_logging(level: str = "INFO"):
    """Adjust log level after config is loaded (file handler already set up)."""
    logging.getLogger().setLevel(getattr(logging, level.upper(), logging.DEBUG))


async def _poll_shutdown(shutdown_event: asyncio.Event):
    """Bridge threading.Event → asyncio.Event (check every 0.5s)."""
    while not _shutdown.is_set():
        await asyncio.sleep(0.5)
    shutdown_event.set()


async def main():
    """Main orchestrator entry point."""

    shutdown_event = asyncio.Event()
    asyncio.create_task(_poll_shutdown(shutdown_event))

    # Load all accounts
    config = build_config(args.accounts)

    # Setup logging with first account's level
    setup_logging(config.accounts[0].log_level)

    logger.info(
        f"Orchestrator started — {len(config.accounts)} accounts: "
        f"{', '.join(a.name for a in config.accounts)}"
    )

    # Initialize unified database
    repo = UnifiedRepo("data/orchestrator.db")

    # Reset any projects stuck in 'analyzing' from a previous crash/kill
    stale = repo.reset_stale_analyzing()
    if stale:
        logger.info(f"Reset {stale} stale 'analyzing' project(s) → pending")

    # Initialize color DB for round-robin project title colors
    from src.services.ai.gemini_analyzer import init_color_db
    init_color_db(repo)

    # Seed default settings for each account
    for acc in config.accounts:
        repo.init_account_defaults(acc.name, {
            "paused": "false",
            "poll_interval": "300",
            "budget_min": "50",
            "budget_max": "1000",
            "min_daily_rate": str(acc.min_daily_rate),
            "max_bid_count": str(acc.max_bid_count),
            "bid_adjustment": "-10",
            "rate_tier2_pct": "65",
            "rate_tier3_pct": "50",
            "verified": "true",
            "skip_preferred_only": "true",
            "auto_bid": "true",
            "notif_mode": "all",
            "max_project_age": str(acc.max_project_age_hours),
        })

    # Initialize per-account services
    all_services = init_all_services(config.accounts)

    # Extract project_services and bidding_services for polling
    project_services = {name: svc["project_service"] for name, svc in all_services.items()}
    bidding_services = {name: svc["bidding_service"] for name, svc in all_services.items()}

    # Create tagger
    tagger = ProjectTagger(config.accounts, repo)

    # Start Telegram bots (one per account, injecting context into bot_data)
    telegram_apps = await start_all_bots(config.accounts, repo, all_services)

    # Start background tasks
    tasks = [
        asyncio.create_task(polling_loop(
            config, repo, tagger, project_services, bidding_services, shutdown_event,
        )),
        asyncio.create_task(analysis_dispatcher(repo, shutdown_event, tagger=tagger, account_services=all_services)),
        asyncio.create_task(bid_dispatcher(config, repo, all_services, shutdown_event, tagger=tagger)),
        # cleanup_loop disabled — accumulate all data for analysis
        # asyncio.create_task(cleanup_loop(repo, shutdown_event)),
    ]

    # Remove any asyncio signal handlers (they replace signal.signal with _sighandler_noop
    # and use a self-pipe that may not be read). Then install a pure Python handler.
    loop = asyncio.get_event_loop()
    try:
        loop.remove_signal_handler(signal.SIGINT)
    except Exception:
        pass
    try:
        loop.remove_signal_handler(signal.SIGTERM)
    except Exception:
        pass
    signal.signal(signal.SIGINT, _sigint_handler)
    signal.signal(signal.SIGTERM, _sigint_handler)

    logger.info("All systems running. Press Ctrl+C to stop.")

    # Wait for shutdown signal
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    # Graceful shutdown — dispatchers see shutdown_event and stop picking new work.
    # Their loops exit naturally, letting in-flight Call 1 / Call 2 / bids finish.
    logger.info("Waiting for active tasks to finish...")

    # Wait for dispatcher loops to exit (they finish current iteration then stop)
    await asyncio.gather(*tasks, return_exceptions=True)

    await stop_all_bots(telegram_apps)

    repo.close()
    logger.info("Shutdown complete")


if __name__ == "__main__":
    # Prevent macOS sleep
    if sys.platform == "darwin":
        import subprocess as _sp
        _caffeinate = _sp.Popen(["caffeinate", "-dimsu", "-w", str(os.getpid())])

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        # signal.signal handler didn't fire (race) — do it here
        _sigint_handler()
    except SystemExit:
        pass  # os._exit from force shutdown
    except Exception as e:
        logger.error(f"Fatal: {type(e).__name__}: {e}")
        logger.exception("Traceback:")
        sys.exit(1)
    finally:
        loop.close()

    sys.exit(0)
