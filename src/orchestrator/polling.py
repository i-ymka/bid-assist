"""Unified polling loop: single Freelancer API call for all accounts."""

import asyncio
import logging
import time
from datetime import datetime

from src.config.loader import OrchestratorConfig
from src.services.storage.unified_repo import UnifiedRepo
from src.services.freelancer.projects import ProjectService
from src.filters.tagger import ProjectTagger

logger = logging.getLogger(__name__)

# Module-level bid cache shared across poll cycles
_bid_caches: dict[str, set] = {}   # account_name -> set of project_ids
_bid_cache_ts: float = 0


async def polling_loop(
    config: OrchestratorConfig,
    repo: UnifiedRepo,
    tagger: ProjectTagger,
    project_services: dict,       # account_name -> ProjectService
    bidding_services: dict,       # account_name -> BiddingService
    shutdown_event: asyncio.Event,
):
    """Single polling loop that fetches projects once and tags them for all accounts.

    Args:
        project_services: Any account's ProjectService works for fetching (API is public).
                          We use the first one.
        bidding_services: Per-account BiddingService to check already-bid projects.
    """
    logger.debug("Unified polling loop started")

    # Use first account's project service for fetching (search API doesn't need auth)
    first_account = config.accounts[0].name
    project_service = project_services[first_account]

    while not shutdown_event.is_set():
        try:
            # Check if ALL accounts paused — if so, sleep
            all_paused = all(repo.is_paused(a.name) for a in config.accounts)
            if all_paused:
                await asyncio.sleep(5)
                continue

            # Use minimum poll interval across active accounts
            intervals = [
                repo.get_poll_interval(a.name)
                for a in config.accounts
                if not repo.is_paused(a.name)
            ]
            interval = min(intervals) if intervals else 300

            # Refresh bid caches (which projects each account already bid on)
            global _bid_caches, _bid_cache_ts
            now_ts = time.time()
            if now_ts - _bid_cache_ts > 600:  # refresh every 10 min
                for acc_name, bservice in bidding_services.items():
                    try:
                        _bid_caches[acc_name] = bservice.get_my_bidded_project_ids(limit=200)
                    except Exception as e:
                        logger.error(f"Failed to fetch bid cache for {acc_name}: {e}")
                        _bid_caches.setdefault(acc_name, set())
                _bid_cache_ts = now_ts

            # Compute merged budget: widest range across non-paused accounts
            budget_mins, budget_maxes = [], []
            for a in config.accounts:
                if not repo.is_paused(a.name):
                    bmin, bmax = repo.get_budget_range(a.name)
                    budget_mins.append(bmin)
                    budget_maxes.append(bmax)
            merged_min = min(budget_mins) if budget_mins else 50
            merged_max = max(budget_maxes) if budget_maxes else 1000

            # Single API call with merged parameters
            projects = project_service.get_active_projects(
                skill_ids=config.merged_skill_ids,
                min_budget=merged_min,
            )

            new_count = 0
            for project in projects:
                pid = project.id

                # Skip if already in DB
                if repo.is_known(pid):
                    continue

                # Skip if ANY account already bid on it
                already_bid = any(
                    pid in _bid_caches.get(a.name, set())
                    for a in config.accounts
                )
                if already_bid:
                    continue

                # Extract skill names and IDs
                skill_names = ",".join([job.name for job in project.jobs])
                skill_ids_str = ",".join([str(job.id) for job in project.jobs])

                # Fetch owner country if unknown
                if not project.owner.country or project.owner.country == "Unknown":
                    owner_country = project_service.get_project_owner_country(project.id)
                    if owner_country:
                        project.owner.country = owner_country
                    else:
                        project.owner.country = "Unknown"

                # Store project in unified DB
                project_data = {
                    "title": project.title,
                    "description": project.description,
                    "budget_min": project.budget.minimum,
                    "budget_max": project.budget.maximum,
                    "currency": project.currency.code,
                    "client_country": project.owner.country,
                    "bid_count": project.bid_stats.bid_count,
                    "avg_bid": project.bid_stats.bid_avg,
                    "url": project.url,
                    "skill_names": skill_names,
                    "skill_ids_str": skill_ids_str,
                    "owner_username": project.owner.username,
                    "owner_display_name": project.owner.display_name or "",
                    "is_preferred_only": project.is_preferred_only,
                    "language": project.language,
                    "time_submitted": project.time_submitted,
                }
                repo.add_project(pid, **project_data)

                # Tag with matching accounts
                tag_data = {**project_data, "project_id": pid}
                tags = tagger.tag_project(tag_data)

                if not tags:
                    # No account wants this project — mark as skipped
                    repo.set_status(pid, "skipped")
                    continue

                new_count += 1
                tag_str = "+".join(sorted(tags))
                logger.debug(f"[cyan]  {project.title[:55]}  [{project.owner.country}] → [{tag_str}][/cyan]")

            pending = len(repo.get_pending_projects(limit=999))
            if new_count > 0:
                logger.debug(f"Polling: +{new_count} queued, {pending} pending")
            else:
                logger.debug(f"Polling: 0 new, {pending} pending")

            # Wait for next cycle
            for _ in range(interval):
                if shutdown_event.is_set():
                    break
                await asyncio.sleep(1)

        except Exception as e:
            if "Timed out" in str(e):
                logger.debug("Polling: Telegram timeout, retrying...")
            else:
                logger.error(f"Polling error: {e}")
            await asyncio.sleep(30)
