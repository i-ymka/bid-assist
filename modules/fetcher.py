import logging
import requests
from typing import List, Dict, Any

from config import FREELANCER_OAUTH_TOKEN, WL_KEYWORDS, MIN_BUDGET
from constants import FREELANCER_API_BASE_URL, PROJECTS_ENDPOINT

def get_new_projects() -> List[Dict[str, Any]]:
    """
    Запрашивает новые проекты с Freelancer.com через API.

    Использует параметры из конфига для первичной фильтрации на стороне API.
    Возвращает список словарей, где каждый словарь - это проект.
    """
    headers = {
        "Freelancer-OAuth-V1": FREELANCER_OAUTH_TOKEN,
        "Content-Type": "application/json",
    }

    # Формируем параметры запроса
    params = {
        "query": " ".join(WL_KEYWORDS), # Поиск по ключевым словам из whitelist
        "project_types[]": "fixed",     # Искать только проекты с фиксированной оплатой
        "min_budget": MIN_BUDGET,       # Минимальный бюджет
        "limit": 50                     # Ограничиваем кол-во проектов за один запрос
    }

    url = f"{FREELANCER_API_BASE_URL}{PROJECTS_ENDPOINT}"
    logging.info(f"Отправка запроса на {url} с параметрами: {params}")

    try:
        response = requests.get(url, headers=headers, params=params, verify=True)

        # Проверка лимитов API
        rate_limit_remaining = response.headers.get('X-RateLimit-Remaining')
        if rate_limit_remaining and int(rate_limit_remaining) < 10:
            logging.warning(f"Осталось мало запросов к API: {rate_limit_remaining}")

        # Обработка ответа
        if response.status_code == 401:
            logging.critical("Ошибка 401 Unauthorized. Ваш FREELANCER_OAUTH_TOKEN недействителен или истек.")
            return []

        response.raise_for_status() # Вызовет исключение для кодов 4xx/5xx

        data = response.json()
        if data.get("status") == "success":
            projects = data.get("result", {}).get("projects", [])
            logging.info(f"API вернуло {len(projects)} проектов.")
            return projects
        else:
            logging.error(f"API вернуло статус 'error': {data.get('message')}")
            return []

    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка сети при запросе к API Freelancer: {e}")
        return []
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при получении проектов: {e}")
        return []