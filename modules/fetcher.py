import logging
import requests
from typing import List, Dict, Any

from config import FREELANCER_OAUTH_TOKEN, WL_KEYWORDS, MIN_BUDGET, SKILL_IDS
from constants import FREELANCER_API_BASE_URL, PROJECTS_ENDPOINT

def get_new_projects() -> List[Dict[str, Any]]:
    """
    Запрашивает новые проекты с Freelancer.com через API,
    используя фильтрацию по ID навыков.
    """
    headers = {
        "Freelancer-OAuth-V1": FREELANCER_OAUTH_TOKEN,
        "Content-Type": "application/json",
    }

    # --- ОБНОВЛЕННЫЕ ПАРАМЕТРЫ ЗАПРОСА ---
    params = {
        "query": " ".join(WL_KEYWORDS),  # Поиск по ключевым словам (дополнительно)
        "jobs[]": SKILL_IDS,  # Фильтрация по ID навыков (САМОЕ ВАЖНОЕ)
        "project_types[]": "fixed",
        "min_budget": MIN_BUDGET,
        "limit": 50
    }

    url = f"{FREELANCER_API_BASE_URL}{PROJECTS_ENDPOINT}"
    logging.info(f"Отправка запроса на {url} с параметрами: {params}")

    try:
        response = requests.get(url, headers=headers, params=params, verify=True)

        rate_limit_remaining = response.headers.get('X-RateLimit-Remaining')
        if rate_limit_remaining and int(rate_limit_remaining) < 10:
            logging.warning(f"Осталось мало запросов к API: {rate_limit_remaining}")

        if response.status_code == 401:
            logging.critical("Ошибка 401 Unauthorized. Ваш FREELANCER_OAUTH_TOKEN недействителен или истек.")
            return []

        response.raise_for_status()

        data = response.json()
        if data.get("status") == "success":
            projects = data.get("result", {}).get("projects", [])
            logging.info(f"API вернуло {len(projects)} проектов по заданным навыкам.")
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