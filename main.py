# bid-assist/main.py (полностью переработанный)

import logging
import asyncio
from telegram import Update
from telegram.ext import Application, ApplicationBuilder, ContextTypes, CallbackQueryHandler

from config import LOG_LEVEL, TELEGRAM_BOT_TOKEN, POLL_INTERVAL
from modules.fetcher import get_new_projects
from modules.store import ProjectStore
from modules.filter import filter_projects
from modules.ai_helper import ask_difficulty, generate_bid
from modules.notifier import send_telegram_notification

# Настройка логирования
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Уменьшаем "шум" от библиотеки httpcore
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Инициализируем хранилище один раз
store = ProjectStore()


async def polling_cycle(context: ContextTypes.DEFAULT_TYPE):
    """
    Основная логика, которая выполняется каждые N секунд.
    Ищет проекты, фильтрует и отправляет уведомления.
    """
    logging.info("--- Начинаю новый цикл опроса проектов ---")

    projects = get_new_projects()
    if not projects:
        return

    unprocessed_projects = [p for p in projects if not store.is_processed(p['id'])]
    if not unprocessed_projects:
        return

    suitable_projects = filter_projects(unprocessed_projects)
    if not suitable_projects:
        return

    logging.info(f"Найдено {len(suitable_projects)} проектов для AI-оценки.")

    for project in suitable_projects:
        project_id = project['id']
        title = project.get('title', '')
        description = project.get('description', '')
        full_text = f"{title}\n\n{description}"

        if ask_difficulty(full_text):
            logging.info(f"Проект ID {project_id} помечен как 'EASY'. Генерирую отклик...")
            draft_bid = generate_bid(title, description)

            # Отправляем уведомление в Telegram
            await send_telegram_notification(project, draft_bid)

            # Сразу добавляем в базу, чтобы не предлагать повторно
            store.add_project(project_id)
        else:
            logging.info(f"Проект ID {project_id} помечен как 'HARD'. Пропускаем.")
            store.add_project(project_id)


async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает нажатия на инлайн-кнопки."""
    query = update.callback_query
    await query.answer()  # Обязательно, чтобы убрать "часики" на кнопке

    action, project_id_str = query.data.split('_')
    project_id = int(project_id_str)

    if action == "send":
        logging.info(f"Получена команда 'Send' для проекта ID {project_id}.")
        # TODO: Реализовать вызов bidder.py
        new_text = query.message.text + "\n\n*Статус: Заявка будет отправлена...*"
        await query.edit_message_text(text=new_text, parse_mode='MarkdownV2', disable_web_page_preview=True)

    elif action == "skip":
        logging.info(f"Получена команда 'Skip' для проекта ID {project_id}.")
        new_text = query.message.text + "\n\n*Статус: Пропущено ❌*"
        # Убираем кнопки после принятия решения
        await query.edit_message_text(text=new_text, parse_mode='MarkdownV2', disable_web_page_preview=True)


def main():
    """Запускает бота."""
    logging.info("Бот 'Bid-Assist' запускается...")

    if not TELEGRAM_BOT_TOKEN:
        logging.critical("TELEGRAM_BOT_TOKEN не найден! Бот не может быть запущен.")
        return

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчик для нажатий на кнопки
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    # Ставим нашу основную логику в очередь задач
    job_queue = application.job_queue
    job_queue.run_repeating(polling_cycle, interval=POLL_INTERVAL, first=10)

    logging.info("Бот запущен и готов к работе. Первый опрос начнется через 10 секунд.")
    application.run_polling()


if __name__ == "__main__":
    main()