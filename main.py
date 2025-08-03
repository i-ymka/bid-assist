# bid-assist/main.py (обновленный)

import time
import logging
from config import POLL_INTERVAL, LOG_LEVEL
from modules.fetcher import get_new_projects
from modules.store import ProjectStore
from modules.filter import filter_projects  # <--- Добавили импорт

# ... (код для настройки логирования остается таким же) ...
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)


def main_loop():
    """Главный рабочий цикл бота."""
    logging.info("Бот 'Bid-Assist' запускается...")
    store = ProjectStore()

    while True:
        try:
            logging.info("Начинаю новый цикл опроса проектов...")

            # 1. Получаем свежие проекты
            projects = get_new_projects()
            if not projects:
                logging.info(f"Новых проектов не найдено. Следующий запуск через {POLL_INTERVAL} секунд.")
                time.sleep(POLL_INTERVAL)
                continue

            # 2. Отфильтровываем уже обработанные
            unprocessed_projects = [p for p in projects if not store.is_processed(p['id'])]
            logging.info(
                f"Получено {len(projects)} проектов, из них {len(unprocessed_projects)} ранее не обрабатывались.")

            if not unprocessed_projects:
                logging.info(f"Новых подходящих проектов нет. Следующий запуск через {POLL_INTERVAL} секунд.")
                time.sleep(POLL_INTERVAL)
                continue

            # 3. Применяем детальные фильтры (blacklist, бюджет)
            suitable_projects = filter_projects(unprocessed_projects)

            if suitable_projects:
                logging.info(f"Найдено {len(suitable_projects)} проектов, готовых к AI-оценке.")
                # TODO: Отправить `suitable_projects` в ai_helper.py
                for project in suitable_projects:
                    logging.debug(f"Проект прошел все фильтры: ID {project['id']}, Title: {project['title']}")

            logging.info(f"Цикл завершен. Следующий запуск через {POLL_INTERVAL} секунд.")
            time.sleep(POLL_INTERVAL)

        except Exception as e:
            logging.error(f"Произошла критическая ошибка в главном цикле: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main_loop()