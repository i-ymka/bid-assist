# main.py (версия без кнопок)

import logging
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from config import LOG_LEVEL, TELEGRAM_BOT_TOKEN, POLL_INTERVAL
from modules.fetcher import get_new_projects_list, get_project_details
from modules.store import ProjectStore
from modules.filter import filter_projects
from modules.ai_helper import get_ai_summary
from modules.notifier import send_telegram_notification

logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("httpcore").setLevel(logging.WARNING)

store = ProjectStore()


async def polling_cycle(context: ContextTypes.DEFAULT_TYPE):
    logging.info("--- Начинаю новый цикл опроса проектов ---")

    projects_list = get_new_projects_list()
    if not projects_list: return

    unprocessed_projects = [p for p in projects_list if not store.is_processed(p['id'])]
    if not unprocessed_projects:
        logging.info("Все полученные проекты уже были обработаны ранее.")
        return

    logging.info(f"Получено {len(unprocessed_projects)} новых проектов. Запрашиваю детали...")

    final_suitable_projects = []
    for project_preview in unprocessed_projects:
        project_id = project_preview['id']

        # Получаем полные детали
        project_details = get_project_details(project_id)
        if not project_details:
            store.add_project(project_id)  # Запоминаем, чтобы не проверять снова
            continue

        # --- ГЛАВНЫЙ МОМЕНТ: ПРИМЕНЯЕМ СТРОГИЙ ФИЛЬТР ---
        # Мы передаем проект в виде списка, так как фильтр ожидает список
        if filter_projects([project_details]):
            final_suitable_projects.append(project_details)
        else:
            logging.info(f"Проект ID {project_id} ('{project_details.get('title')}') отфильтрован финальной проверкой.")
            store.add_project(project_id)  # Запоминаем отфильтрованные тоже

    if not final_suitable_projects:
        logging.info("После детальной проверки подходящих проектов не осталось.")
        return

    logging.info(f"Найдено {len(final_suitable_projects)} проектов для AI-анализа после финальной проверки.")

    for final_project in final_suitable_projects:
        title = final_project.get('title', '')
        description = final_project.get('description', '')
        budget = final_project.get('budget', {})
        min_budget = budget.get('minimum', 0)
        max_budget = budget.get('maximum', 0)

        difficulty_rating, summary, draft_bid = get_ai_summary(
            title=title, description=description, budget_min=min_budget, budget_max=max_budget
        )

        await send_telegram_notification(final_project, draft_bid, difficulty_rating, summary)
        store.add_project(final_project['id'])


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