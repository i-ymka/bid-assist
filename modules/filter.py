# modules/filter.py (ФИНАЛЬНАЯ, "БОЕВАЯ" ВЕРСИЯ БЕЗ ДИАГНОСТИКИ И ANTI-SKILLS)

import logging
from typing import List, Dict, Any

from config import BL_KEYWORDS, MIN_BUDGET, MAX_BUDGET, SKILL_IDS

# Создаем множество из наших проверенных ID для быстрой проверки
REQUIRED_SKILLS_SET = set(SKILL_IDS)


def filter_projects(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Выполняет финальную, строгую проверку проекта на основе полных данных.
    """
    if not projects:
        return []

    suitable_projects = []
    for project in projects:
        # --- "ЖЕЛЕЗНЫЙ ЗАНАВЕС" ---
        project_skills = project.get('jobs')
        if not project_skills:
            continue  # Если у проекта нет навыков, он нам не подходит

        project_skill_ids = {skill['id'] for skill in project_skills}

        # Если нет НИ ОДНОГО совпадения с нашими навыками, проект отбрасывается
        if not REQUIRED_SKILLS_SET.intersection(project_skill_ids):
            continue
        # --- КОНЕЦ "ЖЕЛЕЗНОГО ЗАНАВЕСА" ---

        # --- Остальные проверки (бюджет и черный список) ---
        title = (project.get('title') or '').lower()
        description = (project.get('description') or '').lower()
        project_text = title + ' ' + description

        if any(bl_word and bl_word in project_text for bl_word in BL_KEYWORDS):
            logging.debug(f"Проект ID {project['id']} отфильтрован по blacklist.")
            continue

        budget = project.get('budget', {})
        max_budget = budget.get('maximum', 0)
        if not max_budget or not (MIN_BUDGET <= max_budget <= MAX_BUDGET):
            logging.debug(f"Проект ID {project['id']} отфильтрован по бюджету.")
            continue

        suitable_projects.append(project)

    logging.info(f"Финальная фильтрация завершена. Из {len(projects)} проектов осталось {len(suitable_projects)}.")
    return suitable_projects