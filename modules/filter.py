# modules/filter.py (СПЕЦИАЛЬНАЯ ДИАГНОСТИЧЕСКАЯ ВЕРСИЯ - "ГРОМКИЙ ФИЛЬТР" v2)

import logging
from typing import List, Dict, Any

# --- ИСПРАВЛЕННЫЙ ИМПОРТ ---
# Мы импортируем SKILL_IDS, а не REQUIRED_SKILLS_SET
from config import BL_KEYWORDS, MIN_BUDGET, MAX_BUDGET, SKILL_IDS

# --- Создаем set ЗДЕСЬ, внутри файла ---
REQUIRED_SKILLS_SET = set(SKILL_IDS)


def filter_projects(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not projects:
        return []

    suitable_projects = []
    for project in projects:
        project_id = project.get('id', 'N/A')
        project_title = project.get('title', 'No Title')

        # --- БЛОК ДИАГНОСТИКИ НАВЫКОВ ---
        project_skills = project.get('jobs')
        if not project_skills:
            logging.debug(f"DIAGNOSTIC (ID: {project_id}): Проект отброшен, так как у него нет поля 'jobs'.")
            continue

        project_skill_ids = {skill['id'] for skill in project_skills}
        intersection = REQUIRED_SKILLS_SET.intersection(project_skill_ids)

        if not intersection:
            logging.debug(f"DIAGNOSTIC (ID: {project_id}): Проект отброшен, так как нет пересечения по навыкам.")
            continue

        # ЕСЛИ МЫ ДОШЛИ ДО СЮДА, ЗНАЧИТ, БОТ СЧИТАЕТ ПРОЕКТ РЕЛЕВАНТНЫМ.
        # ТЕПЕРЬ МЫ ЗАСТАВИМ ЕГО ОБЪЯСНИТЬ, ПОЧЕМУ.
        logging.info("=" * 50)
        logging.info(f"!!! DIAGNOSTIC: ПРОЕКТ ПРОШЕЛ ФИЛЬТР ПО НАВЫКАМ !!!")
        logging.info(f"    ID Проекта: {project_id}")
        logging.info(f"    Название: {project_title}")
        logging.info(f"    НАЙДЕННЫЕ СОВПАДЕНИЯ ПО ID НАВЫКОВ: {intersection}")
        logging.info(f"    Все навыки проекта (для справки): {[skill.get('name') for skill in project_skills]}")
        logging.info("=" * 50)
        # --- КОНЕЦ БЛОКА ДИАГНОСТИКИ ---

        # --- Остальные проверки (остаются без изменений) ---
        title = (project.get('title') or '').lower()
        description = (project.get('description') or '').lower()
        project_text = title + ' ' + description

        if any(bl_word and bl_word in project_text for bl_word in BL_KEYWORDS):
            continue

        budget = project.get('budget', {})
        max_budget = budget.get('maximum', 0)
        if not max_budget or not (MIN_BUDGET <= max_budget <= MAX_BUDGET):
            continue

        suitable_projects.append(project)

    return suitable_projects