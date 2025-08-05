# bid-assist/modules/notifier.py

import logging
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, helpers
from typing import Dict, Any

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS

# Создаем один экземпляр бота для всего модуля
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Telegram требует экранирования специальных символов для разметки MarkdownV2
def escape_markdown(text: str) -> str:
    """
    Агрессивно экранирует все специальные символы для Telegram MarkdownV2.
    """
    # Список всех символов, которые Telegram требует экранировать
    escape_chars = r'_*[]()~`>#+-=|{}.!'

    # Создаем безопасную копию текста
    safe_text = str(text)

    # Проходим по каждому спецсимволу и добавляем перед ним \
    for char in escape_chars:
        safe_text = safe_text.replace(char, f'\\{char}')

    return safe_text


async def send_telegram_notification(project: Dict[str, Any], draft_bid: str, difficulty_rating: str):
    """
    Формирует и отправляет уведомление в Telegram с оценкой от AI.
    """
    project_id = project['id']

    # Экранируем все текстовые переменные ПЕРЕД сборкой сообщения
    title = escape_markdown(project.get('title', 'Без заголовка'))

    # --- НАШЕ ИСПРАВЛЕНИЕ ---
    raw_project_url = f"https://www.freelancer.com/projects/{project_id}"
    project_url = escape_markdown(raw_project_url)
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    budget = project.get('budget', {})
    currency = project.get('currency', {}).get('code', 'USD')
    min_budget = budget.get('minimum', 0)
    max_budget = budget.get('maximum', 0)
    budget_str = escape_markdown(f"{min_budget} - {max_budget} {currency}")

    owner = project.get('owner', {})
    #owner_name = escape_markdown(owner.get('username', 'N/A'))

    escaped_draft_bid = escape_markdown(draft_bid)
    escaped_difficulty = escape_markdown(difficulty_rating)

    text = (
        f"*{title}*\n\n"
        f"🧠 *AI Rating:* `{escaped_difficulty}`\n"
        f"💰 *Budget:* {budget_str}\n"
        f"🔗 *Project link:*\n{project_url}\n\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"🤖 *Generated response:*\n"
        f"```\n{escaped_draft_bid}\n```"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ Send bid", callback_data=f"send_{project_id}"),
            InlineKeyboardButton("❌ Skip", callback_data=f"skip_{project_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='MarkdownV2',
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            logging.info(f"Уведомление по проекту ID {project_id} успешно отправлено в чат {chat_id}.")
        except Exception as e:
            logging.error(f"Ошибка при отправке уведомления в чат {chat_id} для проекта ID {project_id}: {e}")