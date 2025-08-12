# modules/fetcher.py

import logging
import requests
from typing import List, Dict, Any

# --- ОБНОВЛЕННЫЙ ИМПОРТ (БЕЗ WL_KEYWORDS) ---
from config import FREELANCER_OAUTH_TOKEN, MIN_BUDGET, SKILL_IDS
from constants import FREELANCER_API_BASE_URL, PROJECTS_ENDPOINT


def get_new_projects() -> List[Dict[str, Any]]:
    """
    Запрашивает новые проекты, полагаясь ИСКЛЮЧИТЕЛЬНО на фильтрацию по ID навыков.
    """
    headers = {
        "Freelancer-OAuth-V1": FREELANCER_OAUTH_TOKEN,
        "Content-Type": "application/json",
    }

    # --- ФИНАЛЬНЫЕ, УПРОЩЕННЫЕ ПАРАМЕТРЫ ЗАПРОСА ---
    params = {
        "jobs[]": SKILL_IDS,  # Наш главный и единственный фильтр
        "project_types[]": "fixed",
        "min_budget": MIN_BUDGET,
        "limit": 50
    }

    url = f"{FREELANCER_API_BASE_URL}{PROJECTS_ENDPOINT}"
    logging.info(f"Отправка запроса на {url} с параметрами: {params}")

    try:
        # ... (остальной код функции try/except остается без изменений)
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