"""Post-Call-1 dispatcher: price check, Call 2, bid placement per account."""

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

from src.config.loader import OrchestratorConfig
from src.services.storage.unified_repo import UnifiedRepo
from src.services.ai.gemini_analyzer import _calculate_amount, write_bid, _title_color, _acct_color
from src.services.currency import to_usd, from_usd, round_up_10
from src.filters.tagger import ProjectTagger

logger = logging.getLogger(__name__)


async def _process_account_bid(
    project: dict,
    account_name: str,
    config: OrchestratorConfig,
    repo: UnifiedRepo,
    account_services: dict,
    loop: asyncio.AbstractEventLoop,
    tagger: ProjectTagger = None,
):
    """Run Call 2 + place bid for one account on one project."""
    pid = project["project_id"]
    acc = config.get_account(account_name)
    services = account_services[account_name]

    try:
        title = project.get("title", "")

        tc = _title_color(pid)
        ac = _acct_color(account_name)

        # Re-check filters before spending AI call
        if tagger:
            reason = tagger._check_filters(acc, project)
            if reason:
                repo.mark_price_fail(pid, account_name)
                logger.info(f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  ({reason})")
                return

        # Per-account pricing
        days = project.get("call1_days") or 1
        avg_bid = project.get("avg_bid") or 0
        budget_min = project.get("budget_min") or 0
        budget_max = project.get("budget_max") or 0
        currency = project.get("currency", "USD")

        # Convert to USD for pricing
        avg_bid_usd = to_usd(avg_bid, currency) if currency != "USD" and avg_bid else avg_bid
        budget_min_usd = to_usd(budget_min, currency) if currency != "USD" and budget_min else budget_min
        budget_max_usd = to_usd(budget_max, currency) if currency != "USD" and budget_max else budget_max

        # Per-account settings from DB
        min_rate = repo.get_min_daily_rate(account_name)
        bid_adj = repo.get_bid_adjustment(account_name)
        tier2 = repo.get_rate_tier2_pct(account_name)
        tier3 = repo.get_rate_tier3_pct(account_name)

        amount_usd = _calculate_amount(
            days=days,
            avg_bid_usd=avg_bid_usd,
            budget_min_usd=budget_min_usd,
            budget_max_usd=budget_max_usd,
            min_daily_rate=min_rate,
            bid_adjustment=bid_adj,
            tier2_pct=tier2,
            tier3_pct=tier3,
            account_name=account_name,
            silent=True,
        )

        floor_usd = days * min_rate
        if amount_usd is None:
            logger.info(
                f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]"
                f"  (floor ${floor_usd:.0f}, {days}d × ${min_rate:.0f}/d)"
            )
            notif_mode = repo.get_notif_mode(account_name)
            if notif_mode in ("all", "bids_plus"):
                notifier = services.get("notifier")
                if notifier:
                    for chat_id in acc.telegram_chat_ids:
                        await notifier.send_skip_notification_to_user(
                            chat_id=chat_id,
                            project_id=pid,
                            title=title,
                            budget_min=budget_min,
                            budget_max=budget_max,
                            currency=currency,
                            client_country=project.get("client_country", "Unknown"),
                            url=project.get("url", ""),
                            summary=project.get("call1_summary", ""),
                        )
            repo.mark_price_fail(pid, account_name)
            return

        logger.info(
            f"[bold green]YEP[/bold green]   [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]"
            f"  ${amount_usd:.0f}  ({days}d)  floor ${floor_usd:.0f}"
        )

        # Convert back to project currency
        if currency != "USD":
            amount = round_up_10(from_usd(amount_usd, currency))
        else:
            amount = amount_usd

        # Call 2: write bid text (blocking — run in executor)
        summary = project.get("call1_summary", "")
        description = project.get("description", "")
        owner_name = project.get("owner_display_name") or project.get("owner_username") or ""

        bid_text, fair_price = await loop.run_in_executor(
            None,
            write_bid,
            pid,
            title,
            description,
            summary,
            amount,
            days,
            owner_name,
            account_name,
        )

        if not bid_text:
            logger.error(f"[{ac}]{account_name}[/{ac}]: call2 failed: [{tc}]{title[:55]}[/{tc}]")
            repo.mark_bid_placed(pid, account_name)  # don't retry
            return

        # Fair price guard: if AI estimates >2x our bid, market rate is way above our floor
        if fair_price and fair_price > amount * 2:
            ratio = fair_price / amount
            logger.info(
                f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]"
                f"  (bid ${amount:.0f}, AI est ${fair_price:.0f} = {ratio:.1f}x)"
            )
            notif_mode = repo.get_notif_mode(account_name)
            if notif_mode in ("all", "bids_plus"):
                notifier = services.get("notifier")
                if notifier:
                    for chat_id in acc.telegram_chat_ids:
                        await notifier.send_skip_notification_to_user(
                            chat_id=chat_id,
                            project_id=pid,
                            title=title,
                            budget_min=budget_min,
                            budget_max=budget_max,
                            currency=currency,
                            client_country=project.get("client_country", "Unknown"),
                            url=project.get("url", ""),
                            summary=summary,
                        )
            repo.mark_price_fail(pid, account_name)
            return

        # Check auto-bid setting
        if not repo.is_auto_bid(account_name):
            # Fetch fresh bid stats so manual notification shows current market data
            bid_count_display = project.get("bid_count", 0)
            avg_bid_display = avg_bid
            project_service = services.get("project_service")
            if project_service:
                try:
                    fresh = await loop.run_in_executor(None, project_service.get_project_details, pid)
                    if fresh and fresh.bid_stats:
                        bid_count_display = fresh.bid_stats.bid_count
                        if fresh.bid_stats.bid_avg:
                            avg_bid_display = fresh.bid_stats.bid_avg
                except Exception:
                    pass

            # Stage for manual Telegram button
            repo.add_pending_bid(
                account_name, pid,
                amount=amount, period=days, description=bid_text,
                title=title, currency=currency, url=project.get("url", ""),
                bid_count=bid_count_display,
                summary=summary,
                budget_min=budget_min, budget_max=budget_max,
                client_country=project.get("client_country", ""),
                avg_bid=avg_bid_display,
            )
            # Send notification via Telegram
            notifier = services.get("notifier")
            bidding_service = services.get("bidding_service")
            if notifier:
                for chat_id in config.get_account(account_name).telegram_chat_ids:
                    result = await notifier.send_gpt_decision_notification_to_user(
                        chat_id=chat_id,
                        project_id=pid,
                        title=title,
                        budget_min=budget_min,
                        budget_max=budget_max,
                        currency=currency,
                        client_country=project.get("client_country", ""),
                        bid_count=bid_count_display,
                        avg_bid=avg_bid_display,
                        url=project.get("url", ""),
                        summary=summary,
                        bid_text=bid_text,
                        suggested_amount=amount,
                        suggested_period=days,
                    )
                    if result:
                        msg, orig_text, orig_keyboard = result
                        if msg and bidding_service:
                            from src.services.telegram.notifier import schedule_bid_update
                            for delay in [60, 300, 600]:
                                asyncio.create_task(schedule_bid_update(
                                    bot=notifier._bot,
                                    chat_id=chat_id,
                                    message_id=msg.message_id,
                                    project_id=pid,
                                    bidding_service=bidding_service,
                                    currency=currency,
                                    original_text=orig_text,
                                    original_keyboard=orig_keyboard,
                                    delay=delay,
                                    account_name=account_name,
                                    title=title,
                                ))
            logger.info(f"[royal_blue1]SENT[/royal_blue1]  [{ac}]{account_name}[/{ac}]: ${amount:.0f}  ({days}d)  [{tc}]{title[:55]}[/{tc}]  (manual)")
            return

        # Bid delay: wait until project is at least N minutes old (auto-bid only)
        bid_delay_min = repo.get_bid_delay(account_name)
        if bid_delay_min > 0:
            ts = project.get("time_submitted")
            if isinstance(ts, str):
                try:
                    ts = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        ts = datetime.fromisoformat(ts)
                    except ValueError:
                        ts = None
            if ts:
                age_secs = (datetime.utcnow() - ts).total_seconds()
                target_secs = bid_delay_min * 60 + random.randint(0, 59)
                wait = target_secs - age_secs
                if wait > 0:
                    logger.info(
                        f"[steel_blue1]WAIT[/steel_blue1]  [{ac}]{account_name}[/{ac}]:"
                        f" [{tc}]{title[:45]}[/{tc}]  ({wait:.0f}s until {bid_delay_min}min)"
                    )
                    await asyncio.sleep(wait)

        # Pre-bid recheck: fresh data from API — all filters + re-price
        project_service = services.get("project_service")
        if project_service:
            fresh = await loop.run_in_executor(None, project_service.get_project_details, pid)
            if not fresh:
                logger.info(f"[yellow3]LATE[/yellow3]   [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  (project closed)")
                repo.mark_bid_placed(pid, account_name)
                return

            # Build dict for tagger filters
            fresh_data = {
                "project_id": pid,
                "title": fresh.title,
                "budget_min": fresh.budget.minimum,
                "budget_max": fresh.budget.maximum,
                "currency": fresh.currency.code,
                "bid_count": fresh.bid_stats.bid_count if fresh.bid_stats else 0,
                "avg_bid": fresh.bid_stats.bid_avg if fresh.bid_stats else 0,
                "client_country": fresh.owner.country,
                "time_submitted": fresh.time_submitted,
                "is_preferred_only": fresh.is_preferred_only,
                "nda_required": fresh.nda_required,
                "language": fresh.language,
                "skill_ids_str": ",".join(str(sid) for sid in fresh.skill_ids),
            }

            # All filters
            if tagger:
                reason = tagger._check_filters(acc, fresh_data)
                if reason:
                    logger.info(f"[yellow3]LATE[/yellow3]   [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  ({reason})")
                    repo.mark_bid_placed(pid, account_name)
                    return

            # Last-mile bid_count check: fresh count from API right before placing
            if fresh.bid_stats:
                max_bids_now = repo.get_max_bid_count(account_name)
                fresh_bid_count = fresh.bid_stats.bid_count
                if fresh_bid_count > max_bids_now:
                    logger.info(
                        f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: "
                        f"[{tc}]{title[:55]}[/{tc}]  ({fresh_bid_count} bids > limit {max_bids_now})"
                    )
                    repo.mark_bid_placed(pid, account_name)
                    return

            # Re-price silently with fresh avg_bid (no extra YEP log)
            fresh_avg = fresh_data["avg_bid"] or 0
            fresh_avg_usd = to_usd(fresh_avg, currency) if currency != "USD" and fresh_avg else fresh_avg
            fresh_amount = _calculate_amount(
                days=days, avg_bid_usd=fresh_avg_usd,
                budget_min_usd=to_usd(fresh_data["budget_min"], currency) if currency != "USD" and fresh_data["budget_min"] else fresh_data["budget_min"],
                budget_max_usd=to_usd(fresh_data["budget_max"], currency) if currency != "USD" and fresh_data["budget_max"] else fresh_data["budget_max"],
                min_daily_rate=min_rate, bid_adjustment=bid_adj,
                tier2_pct=tier2, tier3_pct=tier3, account_name=account_name, silent=True,
            )
            if fresh_amount is None:
                logger.info(f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  (price below floor after recheck)")
                repo.mark_bid_placed(pid, account_name)
                return
            if fresh_amount != amount_usd:
                old_amt = amount if currency == "USD" else amount_usd
                amount_usd = fresh_amount
                amount = round_up_10(from_usd(amount_usd, currency)) if currency != "USD" else amount_usd
                logger.info(f"[{ac}]{account_name}[/{ac}]: [{tc}]{title[:45]}[/{tc}]  price adjusted ${old_amt:.0f} → ${amount:.0f} (fresh avg_bid)")

        # Auto-bid: place bid via Freelancer API
        from src.models import Bid
        bidding_service = services.get("bidding_service")
        notifier = services.get("notifier")
        bid = Bid(
            project_id=pid,
            amount=amount,
            period=days,
            milestone_percentage=acc.default_milestone_pct,
            description=bid_text,
        )
        bid_result = bidding_service.place_bid(bid)

        # Record in bid history
        repo.add_bid_record(
            account_name, pid, amount, days, bid_text, bid_result.success,
            error_message=bid_result.message if not bid_result.success else None,
            title=title, summary=summary, url=project.get("url", ""),
            currency=currency, bid_count=project.get("bid_count", 0),
            budget_min=budget_min, budget_max=budget_max,
            client_country=project.get("client_country", ""),
            avg_bid=avg_bid,
        )

        if bid_result.success:
            repo.mark_bid_placed(pid, account_name, bid_id=bid_result.bid_id)

            # Get rank info and remaining bids
            rank_info = None
            remaining_bids = None
            if bid_result.bid_id:
                try:
                    rank_info = await loop.run_in_executor(
                        None, bidding_service.get_bid_rank,
                        bid_result.bid_id, pid, 1.0,
                    )
                except Exception:
                    pass
                try:
                    remaining_bids = await loop.run_in_executor(
                        None, bidding_service.get_remaining_bids,
                    )
                except Exception:
                    pass

            # Telegram notification
            if notifier:
                for chat_id in acc.telegram_chat_ids:
                    msg, orig_text, orig_keyboard = await notifier.send_auto_bid_notification(
                        chat_id=chat_id,
                        project_id=pid,
                        title=title,
                        budget_min=budget_min,
                        budget_max=budget_max,
                        currency=currency,
                        client_country=project.get("client_country", "Unknown"),
                        bid_count=project.get("bid_count", 0),
                        avg_bid=avg_bid,
                        url=project.get("url", ""),
                        summary=summary,
                        bid_text=bid_text,
                        amount=amount,
                        period=days,
                        bid_id=bid_result.bid_id,
                        rank_info=rank_info,
                        remaining_bids=remaining_bids,
                        fair_price=fair_price,
                    )

                    # Schedule delayed update with fresh stats
                    if msg and bid_result.bid_id:
                        from src.services.telegram.notifier import schedule_price_corrections
                        asyncio.create_task(
                            schedule_price_corrections(
                                bot=notifier._bot,
                                chat_id=chat_id,
                                message_id=msg.message_id,
                                project_id=pid,
                                bid_id=bid_result.bid_id,
                                bidding_service=bidding_service,
                                currency=currency,
                                original_amount=amount,
                                days=days,
                                min_daily_rate=repo.get_min_daily_rate(account_name),
                                original_text=orig_text,
                                original_keyboard=orig_keyboard,
                                account_name=account_name,
                                title=title,
                            )
                        )

            logger.info(f"[royal_blue1]BID [/royal_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  ${amount:.0f}  ({days}d)")

        else:
            repo.mark_bid_placed(pid, account_name)  # don't retry
            error_lower = bid_result.message.lower()

            if "nda" in error_lower or "sign the nda" in error_lower:
                logger.info(f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  (NDA required)")
            elif "preferred freelancer" in error_lower:
                logger.info(f"[slate_blue1]NOPE[/slate_blue1]  [{ac}]{account_name}[/{ac}]: [{tc}]{title[:55]}[/{tc}]  (preferred-only)")
            elif "used all" in error_lower or "all of your bids" in error_lower or ("bid" in error_lower and ("limit" in error_lower or "remain" in error_lower or "run out" in error_lower)):
                repo.set_auto_bid(account_name, False)
                logger.warning(f"[{ac}]{account_name}[/{ac}]: AUTO-BID DISABLED: No bids remaining")
                if notifier:
                    for cid in acc.telegram_chat_ids:
                        await notifier.send_to_user(
                            cid,
                            "⚠️ *Auto\\-bid disabled* — no bids remaining\\. Projects will continue in manual mode\\.",
                        )
            else:
                if notifier:
                    for chat_id in acc.telegram_chat_ids:
                        await notifier.send_auto_bid_failed_notification(
                            chat_id=chat_id,
                            project_id=pid,
                            title=title,
                            url=project.get("url", ""),
                            amount=amount,
                            error=bid_result.message,
                        )
                logger.error(f"[{ac}]{account_name}[/{ac}]: FAIL  [{tc}]{title[:55]}[/{tc}]  — {bid_result.message}")

    except Exception as e:
        _tc = _title_color(pid) if pid else "white"
        _ac = _acct_color(account_name) if account_name else "white"
        logger.error(f"[{_ac}]{account_name}[/{_ac}]: Error bidding: [{_tc}]{title[:55]}[/{_tc}] — {e}")
        repo.mark_bid_placed(pid, account_name)
    finally:
        # If all accounts done → mark project complete
        if not repo.get_unbid_tags(pid):
            repo.set_status(pid, "bidded")


async def bid_dispatcher(
    config: OrchestratorConfig,
    repo: UnifiedRepo,
    account_services: dict,
    shutdown_event: asyncio.Event,
    tagger: ProjectTagger = None,
    check_interval: float = 5.0,
):
    """Picks up done+PASS projects, runs per-account pricing + Call 2 + bidding."""
    logger.debug("Bid dispatcher started")
    loop = asyncio.get_event_loop()
    active_tasks: set[asyncio.Task] = set()

    while not shutdown_event.is_set():
        try:
            done_projects = repo.get_done_projects(limit=10)

            for project in done_projects:
                if shutdown_event.is_set():
                    break

                pid = project["project_id"]
                title = project.get("title", "")
                tagged_accounts = repo.get_tags(pid)
                accounts_to_bid = repo.get_unbid_tags(pid)

                # Log NOPE for untagged accounts (didn't pass filters at polling time)
                if tagger:
                    disp_tc = _title_color(pid)
                    for acc in config.accounts:
                        if acc.name not in tagged_accounts:
                            reason = tagger._check_filters(acc, project) or "unknown"
                            disp_ac = _acct_color(acc.name)
                            logger.info(f"[slate_blue1]NOPE[/slate_blue1]  [{disp_ac}]{acc.name}[/{disp_ac}]: [{disp_tc}]{title[:55]}[/{disp_tc}]  ({reason})")

                if not accounts_to_bid:
                    repo.set_status(pid, "bidded")
                    continue

                # Block re-pickup: status='bidding' prevents get_done_projects from returning this project
                repo.set_status(pid, "bidding")

                for acc_name in accounts_to_bid:
                    if repo.is_paused(acc_name):
                        continue
                    task = asyncio.create_task(
                        _process_account_bid(project, acc_name, config, repo, account_services, loop, tagger=tagger)
                    )
                    active_tasks.add(task)
                    task.add_done_callback(active_tasks.discard)

            await asyncio.sleep(check_interval)

        except Exception as e:
            logger.error(f"Bid dispatcher error: {e}")
            await asyncio.sleep(10)

    # Graceful: wait for in-flight Call 2 + bid tasks to finish
    if active_tasks:
        logger.info(f"Waiting for {len(active_tasks)} active bid tasks...")
        await asyncio.gather(*active_tasks, return_exceptions=True)
