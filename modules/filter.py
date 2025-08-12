# modules/filter.py (финальная версия с "железным" фильтром по навыкам)

import logging
from typing import List, Dict, Any

# Добавляем SKILL_IDS в импорты
from config import BL_KEYWORDS, MIN_BUDGET, MAX_BUDGET, SKILL_IDS

# Превращаем список ID в множество для сверхбыстрой проверки
# Это профессиональная оптимизация, которая ускоряет работу
REQUIRED_SKILLS_SET = set(SKILL_IDS)

# modules/filter.py (финальная версия с защитой от None в навыках)

import logging
from typing import List, Dict, Any

from config import BL_KEYWORDS, MIN_BUDGET, MAX_BUDGET, SKILL_IDS

REQUIRED_SKILLS_SET = set(SKILL_IDS)


def filter_projects(projects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Фильтрует список проектов по blacklist, бюджету и ОБЯЗАТЕЛЬНОМУ
    наличию хотя бы одного навыка из нашего списка.
    """
    if not projects:
        return []

    suitable_projects = []
    for project in projects:
        # --- ИСПРАВЛЕННЫЙ "ЖЕЛЕЗНЫЙ" ФИЛЬТР ---
        project_skills = project.get('jobs')  # Получаем список навыков, который может быть None

        # Если у проекта нет навыков (None или пустой список), пропускаем его
        if not project_skills:
            logging.debug(f"Проект ID {project['id']} отфильтрован: нет списка навыков.")
            continue

        project_skill_ids = {skill['id'] for skill in project_skills}

        if not REQUIRED_SKILLS_SET.intersection(project_skill_ids):
            logging.debug(f"Проект ID {project['id']} отфильтрован: нет совпадений по навыкам.")
            continue
        # --- КОНЕЦ ИСПРАВЛЕННОГО ФИЛЬТРА ---

        title_raw = project.get('title')
        description_raw = project.get('description')
        title = title_raw.lower() if title_raw else ""
        description = description_raw.lower() if description_raw else ""
        project_text = title + ' ' + description

        if any(bl_word and bl_word in project_text for bl_word in BL_KEYWORDS):
            logging.debug(f"Проект ID {project['id']} отфильтрован по blacklist.")
            continue

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