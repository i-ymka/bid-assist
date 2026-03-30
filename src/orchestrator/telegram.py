"""Telegram multi-bot setup: one Application per account in a single process."""

import logging
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import NetworkError, TelegramError
from telegram import Update

from src.config.account import AccountConfig
from src.services.storage.unified_repo import UnifiedRepo
from src.services.telegram.handlers import setup_handlers

logger = logging.getLogger(__name__)


def _build_control_panel() -> tuple:
    """Build the control panel message + keyboard for forum topics."""
    text = "🎛 <b>Control Panel</b>\n\nTap a button below:"
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Status", callback_data="panel:status"),
            InlineKeyboardButton("⚙️ Settings", callback_data="panel:settings"),
        ],
        [
            InlineKeyboardButton("📈 Bid Stats", callback_data="panel:bidstats"),
            InlineKeyboardButton("❓ Help", callback_data="panel:help"),
        ],
    ])
    return text, keyboard


async def _panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route control panel button taps to the actual command handlers.

    Handlers use update.message.reply_text — but callback queries have
    update.callback_query.message. Since Update is frozen, we reply directly.
    """
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[1]
    msg = query.message  # the control panel message itself

    from src.services.telegram.handlers import (
        _build_settings_message, _get_settings_keyboard,
    )
    from src.services.storage.repo_adapter import AccountRepoAdapter

    bd = context.bot_data
    repo = AccountRepoAdapter(bd["repo"], bd["account_name"])

    if action == "status":
        paused = "⏸ Paused" if repo.is_paused() else "▶️ Running"
        auto = "✅" if repo.is_auto_bid() else "❌"
        poll = repo.get_poll_interval()
        bmin, bmax = repo.get_budget_range()
        text = (
            f"<b>Status:</b> {paused}\n"
            f"<b>Auto-bid:</b> {auto}\n"
            f"<b>Poll interval:</b> {poll}s\n"
            f"<b>Budget:</b> ${bmin}–${bmax}"
        )
        await msg.reply_text(text, parse_mode="HTML")
    elif action == "settings":
        text = _build_settings_message(repo)
        keyboard = _get_settings_keyboard(repo)
        await msg.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    elif action == "bidstats":
        # Show period picker (same as /bidstats command)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 All time", callback_data="bidstats:alltime"),
                InlineKeyboardButton("📅 Last 7 days", callback_data="bidstats:weekly"),
            ]
        ])
        await msg.reply_text("Choose period:", reply_markup=keyboard)
    elif action == "help":
        await msg.reply_text(
            "<b>Commands:</b>\n"
            "/status — Status & control\n"
            "/settings — Bot settings\n"
            "/bidstats — Bid history & stats\n"
            "/help — This help",
            parse_mode="HTML",
        )


async def _send_control_panel(app: Application, chat_id: str, thread_id: int):
    """Send a pinned control panel message to the bot's topic."""
    text, keyboard = _build_control_panel()
    try:
        msg = await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=keyboard,
            message_thread_id=thread_id if thread_id else None,
        )
        # Try to pin it
        try:
            await app.bot.pin_chat_message(
                chat_id=chat_id,
                message_id=msg.message_id,
            )
        except Exception:
            pass  # Pin might fail if bot isn't admin
    except Exception as e:
        logger.error(f"Failed to send control panel: {e}")


async def setup_bot(
    account: AccountConfig,
    repo: UnifiedRepo,
    services: dict,
) -> Application:
    """Create and configure a Telegram Application for one account.

    Injects account context into bot_data so handlers can access it via:
        repo = _ctx(context)
        bidding, project_svc = _svc(context)
    """
    app = Application.builder().token(account.telegram_token).build()

    # Inject per-account context into bot_data
    app.bot_data["account_name"] = account.name
    app.bot_data["account_config"] = account
    app.bot_data["repo"] = repo
    app.bot_data["bidding_service"] = services["bidding_service"]
    app.bot_data["project_service"] = services["project_service"]
    app.bot_data["notifier"] = services["notifier"]

    setup_handlers(app)

    # Add control panel callback handler
    app.add_handler(CallbackQueryHandler(_panel_callback, pattern="^panel:"))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    await app.bot.set_my_commands([
        BotCommand("status", "Status & Control"),
        BotCommand("settings", "Bot settings"),
        BotCommand("bidstats", "Bid history"),
        BotCommand("help", "Help"),
    ])

    # Send control panel to the topic (if forum mode)
    if account.telegram_thread_id and account.telegram_chat_ids:
        await _send_control_panel(app, account.telegram_chat_ids[0], account.telegram_thread_id)

    logger.info(f"Telegram bot started for {account.name}")
    return app


async def start_all_bots(accounts, repo, all_services) -> list:
    """Start Telegram bots for all accounts. Returns list of Applications."""
    apps = []
    for acc in accounts:
        try:
            app = await setup_bot(acc, repo, all_services[acc.name])
            apps.append(app)
        except (NetworkError, TelegramError) as e:
            logger.error(f"Telegram bot failed for {acc.name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error starting bot for {acc.name}: {e}")
    return apps


async def stop_all_bots(apps: list):
    """Gracefully stop all Telegram bots."""
    for app in apps:
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")
