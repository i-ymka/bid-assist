# modules/ai_helper.py (переработанная версия)

import logging
import openai
from config import OPENAI_API_KEY, LLM_MODEL, USERNAME, PORTFOLIO_URL

client = None
if OPENAI_API_KEY:
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logging.error(f"Ошибка при инициализации клиента OpenAI: {e}")
else:
    logging.warning("OPENAI_API_KEY не найден. Модуль ai_helper будет работать в режиме деградации.")


def rate_difficulty(project_text: str) -> str:
    """
    Оценивает сложность проекта по шкале и возвращает строку: EASY, MEDIUM, HARD.
    """
    if not client:
        return "N/A"  # Возвращаем "Not Applicable", если нет ключа

    trimmed_text = project_text[:4000]

    prompt = (
        "Rate the difficulty of the following task for a Python developer skilled in automation and APIs. "
        "The rating should be one of three levels:\n"
        "- EASY: Standard task, few unknowns, likely doable in 1 days with or w/o AI help.\n"
        "- MEDIUM: Requires some research, has tricky parts, or involves integrating multiple systems, but possible with AI help.\n"
        "- HARD: Complex, high risk, requires deep specialist knowledge (e.g., legacy systems, complex algorithms, high-load optimization).\n"
        f"Respond with a single word: EASY, MEDIUM, or HARD. Task: {trimmed_text}"
    )

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are a concise tech project evaluator."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=5,
            temperature=0.0
        )
        answer = response.choices[0].message.content.strip().upper()
        # Проверяем, что ответ один из ожидаемых, иначе возвращаем "Unknown"
        if answer in ["EASY", "MEDIUM", "HARD"]:
            logging.info(f"LLM оценила сложность как: {answer}")
            return answer
        else:
            logging.warning(f"LLM вернула неожиданный ответ: '{answer}'. Помечаем как 'Unknown'.")
            return "Unknown"
    except Exception as e:
        logging.error(f"Ошибка при вызове API OpenAI для оценки сложности: {e}")
        return "Error"


def generate_bid(title: str, description: str) -> str:
    """
    Генерирует черновик отклика по очень строгим правилам.
    """
    if not client:
        return "Bid generation is skipped because the OpenAI API key is not configured."

    safe_description = description if description else ""
    trimmed_description = safe_description[:500]

    # --- НОВЫЙ, СТРОГИЙ ПРОМПТ ---
    prompt = (
        f"You are writing a short, direct bid proposal for a project on Freelancer.com.\n"
        f"Your response MUST be 2-3 sentences only.\n\n"
        f"**My Details:**\n"
        f"- Name: {USERNAME}\n"
        f"- Role: Senior Developer\n"
        f"- Portfolio: {PORTFOLIO_URL}\n\n"
        f"**Project Details:**\n"
        f"Title: {title}\n"
        f"Description: {trimmed_description}\n\n"
        f"**Instructions:**\n"
        f"1. Start DIRECTLY with a simple greeting like 'Hello!' or 'Hi,'.\n"
        f"2. Immediately state my name and concisely connect my skills to the project's needs.\n"
        f"3. Mention my portfolio naturally within the text.\n\n"
        f"**CRITICAL RULES: DO NOT...**\n"
        f"- DO NOT use a formal salutation like 'Dear...'.\n"
        f"- DO NOT use a formal closing like 'Best regards,' or 'Sincerely,'.\n"
        f"- DO NOT sign my name at the end. The proposal must end with the last sentence of the main text."
    )

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            # Обновляем системное сообщение для большей точности
            messages=[
                {"role": "system",
                 "content": "You write concise, direct, and informal bid proposals for freelance websites, following all rules strictly."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )
        bid_text = response.choices[0].message.content.strip()
        logging.info("LLM сгенерировала черновик отклика по новому промпту.")
        return bid_text
    except Exception as e:
        logging.error(f"Ошибка при вызове API OpenAI для генерации отклика: {e}")
        return "Error: Could not generate bid text."