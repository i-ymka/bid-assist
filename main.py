# main.py (версия без кнопок)

import logging
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import LOG_LEVEL, TELEGRAM_BOT_TOKEN, POLL_INTERVAL
from modules.fetcher import get_new_projects
from modules.store import ProjectStore
from modules.filter import filter_projects
# Мы пока не делаем объединенный запрос, так что возвращаем старый импорт
from modules.ai_helper import get_ai_summary
from modules.notifier import send_telegram_notification

logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("httpcore").setLevel(logging.WARNING)

store = ProjectStore()

async def polling_cycle(context: ContextTypes.DEFAULT_TYPE):
    """
    Основная логика (финальная версия с 3-частной AI-сводкой).
    """
    logging.info("--- Начинаю новый цикл опроса проектов ---")

    projects = get_new_projects()
    if not projects: return

    unprocessed_projects = [p for p in projects if not store.is_processed(p['id'])]
    if not unprocessed_projects: return

    suitable_projects = filter_projects(unprocessed_projects)
    if not suitable_projects: return

    logging.info(f"Найдено {len(suitable_projects)} проектов для AI-обработки.")

    for project in suitable_projects:
        project_id = project['id']
        title = project.get('title', '')
        description = project.get('description', '')

        # --- Получаем все 3 части от AI ---
        difficulty_rating, summary, draft_bid = get_ai_summary(title, description)

        # --- Передаем все 3 части в уведомление ---
        await send_telegram_notification(project, draft_bid, difficulty_rating, summary)
        store.add_project(project_id)


def main():
    """Запускает бота."""
    logging.info("Бот 'Bid-Assist' запускается...")
    if not TELEGRAM_BOT_TOKEN:
        logging.critical("TELEGRAM_BOT_TOKEN не найден!")
        return

    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # УБРАЛИ обработчик кнопок (CallbackQueryHandler)

    job_queue = application.job_queue
    job_queue.run_repeating(polling_cycle, interval=POLL_INTERVAL, first=5)

    logging.info("Бот запущен. Первый опрос начнется через 5 секунд.")
    application.run_polling()


if __name__ == "__main__":
    main()