"""Parallel Call 1 dispatcher: fires asyncio tasks for feasibility analysis."""

import asyncio
import logging
from typing import Optional

from src.services.storage.unified_repo import UnifiedRepo
from src.services.ai.gemini_analyzer import analyze_feasibility
from src.filters.tagger import ProjectTagger

logger = logging.getLogger(__name__)

# Semaphore: max concurrent Call 1 tasks
_call1_semaphore: asyncio.Semaphore = None

# Set by _run_call1 when all Gemini accounts are exhausted; consumed by analysis_dispatcher.
_exhaustion_event: Optional[asyncio.Event] = None


def _get_exhaustion_event() -> asyncio.Event:
    global _exhaustion_event
    if _exhaustion_event is None:
        _exhaustion_event = asyncio.Event()
    return _exhaustion_event


def _ensure_semaphore(max_concurrent: int = 5):
    global _call1_semaphore
    if _call1_semaphore is None:
        _call1_semaphore = asyncio.Semaphore(max_concurrent)


async def _run_call1(project: dict, repo: UnifiedRepo, loop: asyncio.AbstractEventLoop,
                     tagger: ProjectTagger = None):
    """Run Call 1 for a single project (runs in semaphore-controlled slot)."""
    pid = project["project_id"]
    title = project.get("title", "")

    _ensure_semaphore()
    async with _call1_semaphore:
        # Re-check: does any account still want this project?
        if tagger:
            tags = repo.get_tags(pid)
            # Re-run filters for tagged accounts (settings may have changed)
            still_wanted = False
            for acc in tagger._accounts:
                if acc.name in tags:
                    reason = tagger._check_filters(acc, project)
                    if reason:
                        repo.remove_tag(pid, acc.name)
                        logger.info(f"[slate_blue1]NOPE[/slate_blue1]  {acc.name}: {reason}  (pre-call1 recheck)")
                    else:
                        still_wanted = True
            if not still_wanted:
                repo.set_status(pid, "skipped")
                return

        repo.set_status(pid, "analyzing")

        try:
            # Build budget string for prompt
            bmin = project.get("budget_min") or 0
            bmax = project.get("budget_max") or 0
            currency = project.get("currency", "USD")
            budget_str = f"{currency} {bmin:.0f} – {bmax:.0f}"

            avg_bid = project.get("avg_bid") or 0
            bid_count = project.get("bid_count") or 0

            # Call 1 is blocking (subprocess) — run in executor
            result = await loop.run_in_executor(
                None,
                analyze_feasibility,
                pid,
                title,
                project.get("description", ""),
                budget_str,
                avg_bid,
                bid_count,
            )

            if result:
                repo.store_call1(pid, result["verdict"], result.get("days", 1), result.get("summary", ""))
            else:
                from src.services.ai.gemini_analyzer import _shutdown_flag, consume_exhaustion_flag
                if _shutdown_flag:
                    repo.set_status(pid, "pending")
                elif consume_exhaustion_flag():
                    # All accounts exhausted — reset to pending for retry after pause
                    repo.set_status(pid, "pending")
                    logger.warning(f"All Gemini accounts exhausted (Call 1): {title[:55]} — queued for retry")
                    _get_exhaustion_event().set()
                else:
                    repo.store_call1(pid, "FAILED", 0, "all gemini accounts unavailable")
                    logger.error(f"call1 failed: {title[:55]} — all gemini accounts unavailable")

        except Exception as e:
            logger.error(f"call1 error: {title[:55]} — {e}")
            repo.store_call1(pid, "FAILED", 0, str(e))


async def analysis_dispatcher(
    repo: UnifiedRepo,
    shutdown_event: asyncio.Event,
    tagger: ProjectTagger = None,
    check_interval: float = 5.0,
    account_services: dict = None,
):
    """Continuously picks up pending projects and fires parallel Call 1 tasks.

    Does NOT block on Call 1 completion — fires and moves to next project.
    The semaphore limits concurrent calls.
    """
    logger.debug("Analysis dispatcher started")
    loop = asyncio.get_event_loop()
    active_tasks: set[asyncio.Task] = set()

    while not shutdown_event.is_set():
        try:
            # Check for Gemini exhaustion — pause 30 min and notify
            exhaustion_ev = _get_exhaustion_event()
            if exhaustion_ev.is_set():
                exhaustion_ev.clear()
                logger.warning("All Gemini accounts exhausted — pausing 30 min")
                if account_services:
                    for svc in account_services.values():
                        notifier = svc.get("notifier")
                        if notifier:
                            await notifier.send_quota_exhausted_notification()
                await asyncio.sleep(1800)
                continue

            pending = repo.get_pending_projects(limit=10)

            for project in pending:
                if shutdown_event.is_set():
                    break
                task = asyncio.create_task(_run_call1(project, repo, loop, tagger=tagger))
                active_tasks.add(task)
                task.add_done_callback(active_tasks.discard)

            await asyncio.sleep(check_interval)

        except Exception as e:
            logger.error(f"Analysis dispatcher error: {e}")
            await asyncio.sleep(10)

    # Graceful: wait for in-flight Call 1 tasks to finish
    if active_tasks:
        logger.info(f"Waiting for {len(active_tasks)} active Call 1 tasks...")
        await asyncio.gather(*active_tasks, return_exceptions=True)
