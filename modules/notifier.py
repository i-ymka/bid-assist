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


async def send_telegram_notification(project: Dict[str, Any], draft_bid: str, difficulty_rating: str, summary: str):
    """
    Формирует и отправляет уведомление с ПРАВИЛЬНО экранированным хештегом.
    """
    # ... (код для project_id, title, project_url, budget_str, owner_name без изменений) ...
    project_id = project['id']
    title = escape_markdown(project.get('title', 'No Title'))
    raw_project_url = f"https://www.freelancer.com/projects/{project_id}"
    project_url = escape_markdown(raw_project_url)

    budget = project.get('budget', {})
    currency = project.get('currency', {}).get('code', 'USD')
    min_budget = budget.get('minimum', 0)
    max_budget = budget.get('maximum', 0)
    budget_str = escape_markdown(f"{min_budget} - {max_budget} {currency}")

    owner = project.get('owner', {})
    owner_name = escape_markdown(owner.get('username', 'N/A'))

    # Экранируем все текстовые части
    escaped_summary = escape_markdown(summary)
    escaped_draft_bid = escape_markdown(draft_bid)

    # --- ГЛАВНОЕ ИСПРАВЛЕНИЕ ---
    # Создаем хештег и ЭКРАНИРУЕМ его
    hashtag_rating = f"\\#{difficulty_rating.upper()}"
    # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    text = (
        f"*{title}*\n\n"
        f"📝 *Summary:* {escaped_summary}\n"
        f"💰 *Budget:* {budget_str}\n\n"
        f"🔗 *Project link:*\n{project_url}\n\n"
        f"👇 *Bid:*\n"
        f"```\n{escaped_draft_bid}\n```\n"
        f"{hashtag_rating}"
    )

    # ... (код для цикла отправки без изменений) ...
    for chat_id in TELEGRAM_CHAT_IDS:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='MarkdownV2',
                disable_web_page_preview=True
            )
            logging.info(f"Notification for project ID {project_id} sent successfully to chat {chat_id}.")
        except Exception as e:
            logging.error(f"Error sending notification to chat {chat_id} for project ID {project_id}: {e}")
