# bid-assist/modules/ai_helper.py

import logging
import openai
import config
from config import OPENAI_API_KEY, LLM_MODEL, USERNAME, PORTFOLIO_URL

# Инициализируем клиент OpenAI, если ключ предоставлен
client = None
if OPENAI_API_KEY:
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logging.error(f"Ошибка при инициализации клиента OpenAI: {e}")
else:
    logging.warning("OPENAI_API_KEY не найден. Модуль ai_helper будет работать в режиме деградации.")


def ask_difficulty(project_text: str) -> bool:
    """
    Оценивает, подходит ли проект под персональные навыки пользователя.
    Возвращает True, если проект - "GOOD FIT".
    """
    if not client:
        logging.info("Пропуск оценки сложности: ключ API OpenAI отсутствует.")
        return True

    trimmed_text = project_text[:4000]

    # --- НАШ ПЕРСОНАЛЬНЫЙ ПРОМПТ v4.0 ---
    prompt = (
        "My core skills are Python scripting, building bots, web scraping, and creating workflows in tools like Make/Zapier. "
        "I use AI to help me work faster.\n\n"
        "I am looking for projects that match these skills and can be built from scratch or with standard libraries. "
        "I want to gain practical backend and automation experience.\n\n"
        'GOOD FITS are: "Create a Telegram bot for notifications", "Scrape data from a public website", "Connect API A to Google Sheets".\n'
        'BAD FITS are: "Optimize our complex legacy database", "Debug our custom Salesforce integration", "Build a pixel-perfect front-end UI", '
        '"Add a feature to our platform that the public API doesn\'t support".\n\n'
        "Evaluate the task below. Is it a GOOD FIT or a BAD FIT for me? Respond with only one word: GOOD or BAD. "
        f"Task: {trimmed_text}"
    )

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system",
                 "content": "You are a strict career advisor for a Python developer, filtering out unsuitable jobs."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=5,
            temperature=0.0
        )
        answer = response.choices[0].message.content.strip().upper()
        logging.info(f"LLM (Personal Fit v4.0) evaluated project as: {answer}")
        # Возвращаем True, только если ответ - "GOOD"
        return "GOOD" in answer
    except Exception as e:
        logging.error(f"Ошибка при вызове API OpenAI для оценки сложности: {e}")
        return False

def generate_bid(title: str, description: str) -> str:
    """
    Генерирует черновик отклика на проект.
    """
    if not client:
        logging.info("Пропуск генерации отклика: ключ API OpenAI отсутствует.")
        return "Bid generation is skipped because the OpenAI API key is not configured."

    # Обрезаем описание до 500 символов для промпта
    trimmed_description = description[:500]

    prompt = f"Write a concise, 3-line bid proposal. My name is {USERNAME}. My portfolio is here: {PORTFOLIO_URL}. The project title is '{title}'. The task description starts with: '{trimmed_description}'"

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are writing a bid proposal for a freelance project. Be professional, friendly, and brief."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=150,
            temperature=0.7
        )
        bid_text = response.choices[0].message.content.strip()
        logging.info("LLM сгенерировала черновик отклика.")
        return bid_text
    except Exception as e:
        logging.error(f"Ошибка при вызове API OpenAI для генерации отклика: {e}")
        return "Error: Could not generate bid text."