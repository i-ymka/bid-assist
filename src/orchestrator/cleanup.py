"""Cleanup loop for the unified database."""

import asyncio
import logging

from src.services.storage.unified_repo import UnifiedRepo

logger = logging.getLogger(__name__)


async def cleanup_loop(
    repo: UnifiedRepo,
    shutdown_event: asyncio.Event,
    interval_hours: float = 1.0,
):
    """Periodically clean up old projects, tags, and colors."""
    logger.debug("Cleanup loop started")

    interval_sec = int(interval_hours * 3600)

    while not shutdown_event.is_set():
        try:
            removed = repo.cleanup_old(max_age_hours=24)
            if removed:
                logger.info(f"Cleanup: removed {removed} old projects")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

        for _ in range(interval_sec):
            if shutdown_event.is_set():
                break
            await asyncio.sleep(1)
