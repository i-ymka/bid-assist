# bid-assist/main.py (финальная версия с bidder'ом)

import logging
import asyncio
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, CallbackQueryHandler

from config import LOG_LEVEL, TELEGRAM_BOT_TOKEN, POLL_INTERVAL
from modules.fetcher import get_new_projects
from modules.store import ProjectStore
from modules.filter import filter_projects
from modules.ai_helper import rate_difficulty, generate_bid
from modules.notifier import send_telegram_notification, escape_markdown
from modules.bidder import send_bid  # <--- Добавили импорт

# Настройка логирования
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("httpcore").setLevel(logging.WARNING)

store = ProjectStore()


async def polling_cycle(context: ContextTypes.DEFAULT_TYPE):
    """
    Основная логика (версия "AI-советник").
    Ищет, фильтрует, получает оценку и отправляет ВСЕ подходящие проекты.
    """
    logging.info("--- Начинаю новый цикл опроса проектов ---")

    projects = get_new_projects()
    if not projects: return

    unprocessed_projects = [p for p in projects if not store.is_processed(p['id'])]
    if not unprocessed_projects: return

    suitable_projects = filter_projects(unprocessed_projects)
    if not suitable_projects: return

    logging.info(f"Найдено {len(suitable_projects)} проектов для AI-оценки и отправки.")

    # НОВЫЙ ЦИКЛ: больше нет if/else, отправляем каждый проект
    for project in suitable_projects:
        project_id = project['id']
        title = project.get('title', '')
        description = project.get('description', '')
        full_text = f"{title}\n\n{description}"

        # 1. Получаем оценку от AI
        difficulty_rating = rate_difficulty(full_text)

        # 2. Генерируем отклик
        draft_bid = generate_bid(title, description)

        # 3. Сохраняем данные для отправки ставки (как и раньше)
        max_budget = project.get('budget', {}).get('maximum', 0)
        context.bot_data.setdefault('pending_bids', {})[project_id] = {
            'draft_bid': draft_bid,
            'amount': max_budget
        }

        # 4. Отправляем уведомление в Telegram с новой информацией
        await send_telegram_notification(project, draft_bid, difficulty_rating)

        # 5. Сразу добавляем в базу, чтобы не предлагать повторно
        store.add_project(project_id)


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на инлайн-кнопки."""
    query = update.callback_query
    await query.answer()

    action, project_id_str = query.data.split('_')
    project_id = int(project_id_str)

    original_text = query.message.text

    if action == "send":
        logging.info(f"Получена команда 'Send' для проекта ID {project_id}.")

        # Извлекаем данные, которые сохранили ранее
        bid_data = context.bot_data.get('pending_bids', {}).pop(project_id, None)

        if not bid_data:
            final_text = original_text + "\n\n*Статус: Ошибка! 🤷‍♂️ Данные для ставки устарели (возможно, бот перезапускался).*"
            await query.edit_message_text(text=final_text, parse_mode='MarkdownV2', disable_web_page_preview=True)
            return

        # Вызываем наш новый модуль!
        success, message = send_bid(
            project_id=project_id,
            bid_text=bid_data['draft_bid'],
            amount=bid_data['amount']
        )

        status_message = escape_markdown(message)
        final_text = original_text + f"\n\n*Статус: {status_message}*"

    elif action == "skip":
        logging.info(f"Получена команда 'Skip' для проекта ID {project_id}.")
        # Удаляем данные, так как они больше не нужны
        context.bot_data.get('pending_bids', {}).pop(project_id, None)
        final_text = original_text + "\n\n*Статус: Пропущено ❌*"

    # Убираем кнопки и обновляем сообщение
    await query.edit_message_text(text=final_text, parse_mode='MarkdownV2', disable_web_page_preview=True)


def main():
    """Запускает бота."""
    logging.info("Бот 'Bid-Assist' запускается...")
    if not TELEGRAM_BOT_TOKEN:
        logging.critical("TELEGRAM_BOT_TOKEN не найден!")
        return

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    job_queue = application.job_queue
    job_queue.run_repeating(polling_cycle, interval=POLL_INTERVAL, first=5)

    logging.info("Бот запущен. Первый опрос начнется через 5 секунд.")
    application.run_polling()


if __name__ == "__main__":
    main()