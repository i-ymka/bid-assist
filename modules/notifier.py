# bid-assist/modules/notifier.py

import logging
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, helpers
from typing import Dict, Any

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Создаем один экземпляр бота для всего модуля
bot = Bot(token=TELEGRAM_BOT_TOKEN)

# Telegram требует экранирования специальных символов для разметки MarkdownV2
def escape_markdown(text: str) -> str:
    """Экранирует специальные символы для Telegram MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return helpers.escape_markdown(str(text), version=2, entity_type=helpers.MessageEntityType.TEXT)


async def send_telegram_notification(project: Dict[str, Any], draft_bid: str):
    """
    Формирует и отправляет уведомление в Telegram с инлайн-кнопками.
    """
    project_id = project['id']
    title = escape_markdown(project.get('title', 'Без заголовка'))

    # Формируем URL проекта
    project_url = f"https://www.freelancer.com/projects/{project_id}"

    # Формируем информацию о бюджете
    budget = project.get('budget', {})
    currency = project.get('currency', {}).get('code', 'USD')
    min_budget = budget.get('minimum', 0)
    max_budget = budget.get('maximum', 0)
    budget_str = escape_markdown(f"{min_budget} - {max_budget} {currency}")

    # Информация о заказчике
    owner = project.get('owner', {})
    owner_name = escape_markdown(owner.get('username', 'N/A'))

    # Формируем текст сообщения
    text = (
        f"*{title}*\n\n"
        f"*Бюджет:* {budget_str}\n"
        f"*Заказчик:* {owner_name}\n\n"
        f"*🔗 Ссылка на проект:*\n{project_url}\n\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"*🤖 Сгенерированный отклик:*\n"
        f"```\n{escape_markdown(draft_bid)}\n```"
    )

    # Создаем инлайн-кнопки
    keyboard = [
        [
            InlineKeyboardButton("✅ Отправить отклик", callback_data=f"send_{project_id}"),
            InlineKeyboardButton("❌ Пропустить", callback_data=f"skip_{project_id}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode='MarkdownV2',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
        logging.info(f"Уведомление по проекту ID {project_id} успешно отправлено в Telegram.")
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления в Telegram для проекта ID {project_id}: {e}")