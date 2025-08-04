# modules/filter.py

import logging
from typing import List, Dict, Any

from config import BL_KEYWORDS, MIN_BUDGET, MAX_BUDGET


def filter_projects(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Фильтрует список проектов по заданным критериям (blacklist, бюджет).
    """
    if not projects:
        return []

    suitable_projects = []
    for project in projects:
        # --- ИСПРАВЛЕННЫЙ БЛОК ---
        # Более надежное получение текста, защищенное от None
        title_raw = project.get('title')
        description_raw = project.get('description')

        title = title_raw.lower() if title_raw else ""
        description = description_raw.lower() if description_raw else ""
        # --- КОНЕЦ ИСПРАВЛЕННОГО БЛОКА ---

        project_text = title + ' ' + description

        # 1. Проверка по черному списку (blacklist)
        # Проверяем, что есть слова для проверки и что они не пустые
        if any(bl_word and bl_word in project_text for bl_word in BL_KEYWORDS):
            logging.debug(f"Проект ID {project['id']} отфильтрован по blacklist.")
            continue

        # 2. Проверка бюджета
        budget = project.get('budget', {})
        max_budget = budget.get('maximum', 0)

        if not max_budget:
            logging.debug(f"Проект ID {project['id']} отфильтрован, т.к. нет max_budget.")
            continue

        if not (MIN_BUDGET <= max_budget <= MAX_BUDGET):
            logging.debug(f"Проект ID {project['id']} отфильтрован по бюджету (max: ${max_budget}).")
            continue

        suitable_projects.append(project)

    logging.info(f"Фильтрация завершена. Из {len(projects)} проектов осталось {len(suitable_projects)}.")
    return suitable_projects