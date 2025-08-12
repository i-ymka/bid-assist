# modules/fetcher.py (версия, основанная на УСПЕШНОМ тесте)
import logging
import requests
from typing import List, Dict, Any, Optional
from config import FREELANCER_OAUTH_TOKEN, SKILL_IDS, MIN_BUDGET

def get_new_projects_list() -> List[Dict[str, Any]]:
    """Этап 1: Получает СПИСОК потенциальных проектов."""
    headers = {"Freelancer-OAuth-V1": FREELANCER_OAUTH_TOKEN}
    params = {
        "jobs[]": SKILL_IDS,
        "project_types[]": "fixed",
        "min_budget": MIN_BUDGET,
        "limit": 50
    }
    url = "https://www.freelancer.com/api/projects/0.1/projects/active/"
    logging.info(f"Этап 1: Запрос списка проектов. Параметры: {params}")
    try:
        response = requests.get(url, headers=headers, params=params, verify=True)
        response.raise_for_status()
        data = response.json()
        return data.get("result", {}).get("projects", [])
    except Exception as e:
        logging.error(f"Этап 1: Ошибка при получении списка: {e}")
        return []

def get_project_details(project_id: int) -> Optional[Dict[str, Any]]:
    """Этап 2: Получает ПОЛНУЮ информацию об одном проекте."""
    headers = {"Freelancer-OAuth-V1": FREELANCER_OAUTH_TOKEN}
    url = f"https://www.freelancer.com/api/projects/0.1/projects/{project_id}/"
    # --- ПАРАМЕТРЫ ИЗ УСПЕШНОГО ТЕСТА ---
    params = {"full_description": "true", "job_details": "true"}
    logging.info(f"Этап 2: Запрос деталей для ID {project_id}.")
    try:
        response = requests.get(url, headers=headers, params=params, verify=True)
        response.raise_for_status()
        data = response.json()
        return data.get("result")
    except Exception as e:
        logging.error(f"Этап 2: Ошибка при получении деталей для ID {project_id}: {e}")
        return None