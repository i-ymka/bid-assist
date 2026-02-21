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

import asyncio
import logging
import signal
import sys
from datetime import datetime, timedelta

from telegram import BotCommand
from telegram.ext import Application
from telegram.error import NetworkError, TelegramError

from src.config import settings
from src.services.freelancer import FreelancerClient, ProjectService, BiddingService
from src.services.storage import ProjectRepository
from src.services.telegram.handlers import setup_handlers
from src.services.telegram.notifier import Notifier
from src.services.ai.gemini_analyzer import analyze_project
from src.models import AIAnalysis
from src.models.bid import Bid, Verdict
from src.filters import CountryFilter, BudgetFilter, BlacklistFilter

# Configure logging with file output
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot_debug.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.CRITICAL)

# Global flag for graceful shutdown
shutdown_event = asyncio.Event()


async def polling_loop(repo: ProjectRepository, project_service: ProjectService, bidding_service: BiddingService):
    """Background task that polls Freelancer API for new projects."""
    logger.info("Polling loop started")

    while not shutdown_event.is_set():
        try:
            # Check if paused
            if repo.is_paused():
                await asyncio.sleep(5)
                continue

            interval = repo.get_poll_interval()
            logger.info(f"--- Polling cycle (interval: {interval}s) ---")

            # Use skill_ids from .env
            skill_ids = settings.skill_ids

            # Fetch projects already bid on from Freelancer API
            already_bid_ids = bidding_service.get_my_bidded_project_ids(limit=200)

            # Fetch projects
            projects = project_service.get_active_projects(
                skill_ids=skill_ids,
                min_budget=50,  # Default: $50 (actual filter in BudgetFilter uses runtime settings)
            )

            # Initialize filters
            budget_filter = BudgetFilter()
            blacklist_filter = BlacklistFilter()
            country_filter = CountryFilter()

            new_count = 0
            filtered_count = 0
            already_bid_count = 0
            for project in projects:
                # Skip if already processed or in queue
                if repo.is_processed(project.id) or repo.is_in_queue(project.id):
                    continue

                # Skip if already bid on (from Freelancer API)
                if project.id in already_bid_ids:
                    logger.info(f"SKIPPED project {project.id}: already bid on")
                    repo.add_processed_project(project.id)
                    already_bid_count += 1
                    continue

                # Apply budget filter (no API call needed)
                if not budget_filter.passes(project):
                    reason = budget_filter.get_rejection_reason(project)
                    logger.info(f"FILTERED project {project.id}: {reason}")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply currency filter
                if settings.blocked_currencies:
                    project_currency = project.currency.code.upper()
                    if project_currency in settings.blocked_currencies:
                        logger.info(f"FILTERED project {project.id}: Currency '{project_currency}' is blocked")
                        repo.add_processed_project(project.id)
                        filtered_count += 1
                        continue

                # Apply language filter (check before AI analysis)
                if settings.allowed_languages:
                    project_lang = project.language.lower()
                    if project_lang not in settings.allowed_languages:
                        logger.info(f"FILTERED project {project.id}: Language '{project_lang}' not in allowed list {settings.allowed_languages}")
                        repo.add_processed_project(project.id)
                        filtered_count += 1
                        continue

                # Apply max bid count filter
                if project.bid_stats.bid_count > settings.max_bid_count:
                    logger.info(f"FILTERED project {project.id}: Too many bids ({project.bid_stats.bid_count} > {settings.max_bid_count})")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply project age filter
                if project.time_submitted:
                    age_hours = (datetime.utcnow() - project.time_submitted).total_seconds() / 3600
                    if age_hours > settings.max_project_age_hours:
                        logger.info(f"FILTERED project {project.id}: Too old ({age_hours:.1f}h > {settings.max_project_age_hours}h)")
                        repo.add_processed_project(project.id)
                        filtered_count += 1
                        continue

                # Apply preferred-only filter
                if repo.skip_preferred_only() and project.is_preferred_only:
                    logger.info(f"FILTERED project {project.id}: Preferred freelancer only (upgrades: {project.upgrades})")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply blacklist filter
                if not blacklist_filter.passes(project):
                    reason = blacklist_filter.get_rejection_reason(project)
                    logger.info(f"FILTERED project {project.id}: {reason}")
                    repo.add_processed_project(project.id)
                    filtered_count += 1
                    continue

                # Apply verification filter (skip crypto projects if not verified)
                if not repo.is_verified() and settings.verification_keywords:
                    text_to_check = f"{project.title} {project.description}".lower()
                    skill_names = " ".join([job.name.lower() for job in project.jobs])
                    text_to_check += f" {skill_names}"

                    requires_verification = False
                    for keyword in settings.verification_keywords:
                        if keyword in text_to_check:
                            logger.info(f"FILTERED project {project.id}: Requires verified account (keyword: '{keyword}')")
                            repo.add_processed_project(project.id)
                            filtered_count += 1
                            requires_verification = True
                            break
                    if requires_verification:
                        continue

                # Fetch owner country (API doesn't include it in active projects)
                # This is needed for accurate country filtering
                logger.debug(f"Project {project.id}: initial country = '{project.owner.country}'")
                if not project.owner.country or project.owner.country == "Unknown":
                    owner_country = project_service.get_project_owner_country(project.id)
                    logger.debug(f"Project {project.id}: fetched country = '{owner_country}'")
                    if owner_country:
                        project.owner.country = owner_country
                    else:
                        project.owner.country = "Unknown"
                logger.info(f"Project {project.id}: final country = '{project.owner.country}'")

                # Apply country filter
                if not country_filter.passes(project):
                    reason = country_filter.get_rejection_reason(project)
                    logger.info(f"FILTERED project {project.id}: {reason}")
                    repo.add_processed_project(project.id)  # Mark as processed so we don't fetch again
                    filtered_count += 1
                    continue
                logger.info(f"PASSED project {project.id} from {project.owner.country}")

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
                )
                new_count += 1

            pending = repo.get_queue_count("pending")
            logger.info(f"Polling complete: {new_count} new, {filtered_count} filtered, {already_bid_count} already bid, {pending} pending")

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


async def analysis_loop(repo: ProjectRepository, notifier: Notifier):
    """Background task that analyzes projects with Gemini AI."""
    logger.info("Analysis loop started")

    while not shutdown_event.is_set():
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
            logger.info(f"Analyzing: {project_data['title'][:50]}...")

            # Mark as analyzing
            repo.mark_queue_status(project_id, "analyzing")

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

            if currency != "USD" and (budget_min or budget_max):
                logger.info(f"Currency conversion: {budget_min:.0f}-{budget_max:.0f} {currency} → {budget_min_usd:.0f}-{budget_max_usd:.0f} USD")

            if budget_min_usd and budget_max_usd:
                budget_str = f"{budget_min_usd:.0f} - {budget_max_usd:.0f} USD"
            elif budget_max_usd:
                budget_str = f"up to {budget_max_usd:.0f} USD"
            else:
                budget_str = "Not specified"

            # Run Gemini analysis (blocking, run in thread pool)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                analyze_project,
                project_id,
                project_data["title"],
                project_data["description"],
                budget_str,
                avg_bid_usd,
                bid_count,
            )

            if not result:
                logger.error(f"Analysis failed for {project_id}")
                repo.remove_from_queue(project_id)
                # Don't add to processed — next poll cycle will retry if project still passes filters
                continue

            # Convert AI's USD amount back to project currency
            if currency != "USD" and result.verdict == "BID" and result.amount > 0:
                original_usd = result.amount
                result.amount = round_up_10(from_usd(result.amount, currency))
                logger.info(f"Amount conversion: {original_usd:.0f} USD → {result.amount} {currency}")

            # Mark as processed
            repo.mark_queue_status(project_id, "processed")
            repo.add_processed_project(project_id)

            # Send notification based on verdict
            if result.verdict == "BID":
                # Check auto-bid mode
                if repo.is_auto_bid():
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
                            msg = await notifier.send_auto_bid_notification(
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
                            )
                            if msg:
                                notif_sent = True

                            # Schedule delayed update with fresh stats
                            if msg and bid_result.bid_id:
                                from src.services.telegram.notifier import schedule_bid_update
                                asyncio.create_task(
                                    schedule_bid_update(
                                        bot=notifier._bot,
                                        chat_id=chat_id,
                                        message_id=msg.message_id,
                                        project_id=project_id,
                                        bid_id=bid_result.bid_id,
                                        bidding_service=bidding_service,
                                        currency=currency,
                                        original_text=getattr(msg, '_original_md_text', None),
                                        original_keyboard=getattr(msg, '_original_keyboard', None),
                                    )
                                )

                            logger.info(f"AUTO-BID SUCCESS: {project_id} - ${result.amount} (remaining: {remaining_bids})")
                        else:
                            # Check if error is about bid limit
                            error_lower = bid_result.message.lower()
                            if "used all" in error_lower or "all of your bids" in error_lower or ("bid" in error_lower and ("limit" in error_lower or "remain" in error_lower or "run out" in error_lower)):
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
                            logger.error(f"AUTO-BID FAILED: {project_id} - {bid_result.message}")

                    # Mark notification as sent in bid_history
                    if notif_sent:
                        repo.mark_notification_sent(project_id)
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
                        msg = await notifier.send_gpt_decision_notification_to_user(
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
                                    original_text=getattr(msg, '_original_md_text', None),
                                    original_keyboard=getattr(msg, '_original_keyboard', None),
                                )
                            )
                        logger.info(f"BID notification sent to {chat_id} for {project_id}")
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
                    logger.info(f"SKIP notification sent for {project_id}")
                else:
                    logger.info(f"SKIP notification muted for {project_id}")

        except Exception as e:
            logger.error(f"Analysis error: {e}")
            await asyncio.sleep(10)


async def cleanup_loop(repo: ProjectRepository):
    """Background task that cleans up old data."""
    logger.info("Cleanup loop started")

    while not shutdown_event.is_set():
        try:
            # Clean up every hour
            await asyncio.sleep(3600)

            removed = repo.cleanup_old_queue_items(max_age_hours=24)
            if removed > 0:
                logger.info(f"Cleaned up {removed} old projects")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")


async def main():
    """Main entry point."""
    logger.info("=" * 50)
    logger.info("Bid-Assist starting...")
    logger.info("=" * 50)
    logger.info("Filter settings:")
    logger.info("  Budget, poll interval: configured in bot via /settings")
    logger.info(f"  Max project age: {settings.max_project_age_hours}h")
    logger.info(f"  Max bid count: {settings.max_bid_count}")
    logger.info(f"  Blacklist: {settings.blacklist_keywords or '(none)'}")
    logger.info(f"  Skills: {len(settings.skill_ids)} configured")
    logger.info("=" * 50)

    # Initialize services
    repo = ProjectRepository()
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

    # Log chat IDs
    logger.info(f"Telegram chat IDs: {settings.telegram_chat_ids}")

    # Record bot start time
    repo.set_bot_start_time()

    # Log country filter settings
    logger.info(f"Country filter:")
    logger.info(f"  Blocked: {settings.blocked_countries or '(none)'}")
    logger.info(f"  Allowed: {settings.allowed_countries or '(all)'}")
    logger.info(f"  Block unknown: {settings.block_unknown_countries}")
    logger.info(f"Currency filter: blocked {settings.blocked_currencies or '(none)'}")
    logger.info(f"Language filter: {settings.allowed_languages or '(all)'}")
    logger.info(f"Verified account: {repo.is_verified()}")
    if not repo.is_verified():
        logger.info(f"  Filtering keywords: {settings.verification_keywords}")
    logger.info(f"Skip preferred-only projects: {repo.skip_preferred_only()}")

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

    logger.info("Telegram bot started")
    logger.info("Press Ctrl+C to stop")

    # Start background tasks
    tasks = [
        asyncio.create_task(polling_loop(repo, project_service, bidding_service)),
        asyncio.create_task(analysis_loop(repo, notifier)),
        asyncio.create_task(cleanup_loop(repo)),
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
