# bid-assist/main.py (обновленный)

import time
import logging
from config import POLL_INTERVAL, LOG_LEVEL
from modules.fetcher import get_new_projects
from modules.store import ProjectStore

# Настройка логирования
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

            # Отфильтровываем уже обработанные
            new_projects_count = 0
            unprocessed_projects = []
            for p in projects:
                if not store.is_processed(p['id']):
                    unprocessed_projects.append(p)
                    new_projects_count += 1

            logging.info(f"Получено {len(projects)} проектов, из них {new_projects_count} ранее не обрабатывались.")

            # TODO: Далее эти `unprocessed_projects` пойдут в filter.py и ai_helper.py

            logging.info(f"Цикл завершен. Следующий запуск через {POLL_INTERVAL} секунд.")
            time.sleep(POLL_INTERVAL)

        except Exception as e:
            logging.error(f"Произошла критическая ошибка в главном цикле: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    main_loop()