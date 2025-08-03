# bid-assist/main.py (обновленный)

import time
import logging
from config import POLL_INTERVAL, LOG_LEVEL
from modules.fetcher import get_new_projects
from modules.store import ProjectStore
from modules.filter import filter_projects
from modules.ai_helper import ask_difficulty, generate_bid  # <--- Добавили импорт

# ... (код для настройки логирования) ...
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()])


def main_loop():
    """Главный рабочий цикл бота."""
    logging.info("Бот 'Bid-Assist' запускается...")
    store = ProjectStore()

    while True:
        try:
            logging.info("--- Начинаю новый цикл ---")

            projects = get_new_projects()
            if not projects:
                time.sleep(POLL_INTERVAL)
                continue

            unprocessed_projects = [p for p in projects if not store.is_processed(p['id'])]
            logging.info(f"Получено {len(projects)} проектов, из них {len(unprocessed_projects)} новых.")

            if not unprocessed_projects:
                time.sleep(POLL_INTERVAL)
                continue

            suitable_projects = filter_projects(unprocessed_projects)
            if not suitable_projects:
                logging.info("Проектов, прошедших фильтрацию, нет.")
                time.sleep(POLL_INTERVAL)
                continue

            logging.info(f"Найдено {len(suitable_projects)} проектов для AI-оценки.")

            # 4. AI-обработка
            for project in suitable_projects:
                project_id = project['id']
                title = project.get('title', '')
                description = project.get('description', '')

                # Собираем текст для анализа
                full_text = f"{title}\n\n{description}"

                # Оцениваем сложность
                if ask_difficulty(full_text):
                    logging.info(f"Проект ID {project_id} помечен как 'EASY'. Генерирую отклик...")

                    # Генерируем черновик отклика
                    draft_bid = generate_bid(title, description)

                    logging.info(f"--- ГОТОВО К ОТПРАВКЕ (ID: {project_id}) ---")
                    logging.info(f"Проект: {title}")
                    logging.info(f"Черновик отклика:\n{draft_bid}")

                    # TODO: Отправить `project` и `draft_bid` в notifier.py
                    # TODO: После отправки добавить project_id в store

                else:
                    logging.info(f"Проект ID {project_id} помечен как 'HARD'. Пропускаем.")
                    # Добавляем в базу, чтобы не проверять его снова
                    store.add_project(project_id)

            logging.info(f"--- Цикл завершен. Следующий запуск через {POLL_INTERVAL} секунд. ---")
            time.sleep(POLL_INTERVAL)

        except Exception as e:
            logging.error(f"Произошла критическая ошибка в главном цикле: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main_loop()