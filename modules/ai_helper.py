# modules/ai_helper.py (финальная версия с 3-частным ответом)

import logging
import openai
from typing import Tuple
from config import OPENAI_API_KEY, LLM_MODEL, USERNAME, PORTFOLIO_URL

client = None
if OPENAI_API_KEY:
    try:
        client = openai.OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        logging.error(f"Ошибка при инициализации клиента OpenAI: {e}")
else:
    logging.warning("OPENAI_API_KEY не найден. Модуль ai_helper будет работать в режиме деградации.")


def get_ai_summary(title: str, description: str) -> Tuple[str, str, str]:
    """
    ОДНИМ запросом к LLM получает рейтинг, умное резюме и ГИПЕР-СПЕЦИФИЧНЫЙ отклик.
    """
    if not client:
        return ("N/A", "AI is not configured.", "AI is not configured.")

    safe_description = description if description else ""
    trimmed_description = safe_description[:2000]

    # --- УЛЬТИМАТИВНЫЙ ПРОМПТ С ПЕРСОНОЙ И СТИЛЕМ ---
    prompt = (
        f"You are my expert freelance assistant named 'BlueLion'. Your tone is confident, direct, and hyper-specific. Use simple English.\n"
        f"Analyze the project below and provide three outputs separated by '---'.\n\n"
        f"**Project Details:**\n"
        f"- Title: {title}\n"
        f"- Description: {trimmed_description}\n\n"
        f"--- TASK 1: Difficulty Rating ---\n"
        f"Rate the task difficulty for accomplish this task with the help of ai if I will follow all its instructions (EASY, MEDIUM, HARD).\n\n"
        f"--- TASK 2: Conversational Summary ---\n"
        f"Explain the project's goal to me in a friendly, conversational tone, as if talking to a colleague. Get straight to the point. "
        f"**AVOID** robotic phrases like 'The project aims to' or 'The project involves'. Instead, say something like 'You will do...' or 'They want a...'. "
        f"Mention if it's a new build, a fix, or a long-term role.\n\n"
        f"--- TASK 3: Hyper-Specific Bid Proposal ---\n"
        f"Write a 2-3 sentence bid proposal from my persona. It MUST be confident, friendly and directly reference key technologies from the project description.\n"
        f"**CRITICAL:** Do NOT just say 'I'm skilled in Python'. If the project is about 'PyQt', say 'I have strong experience with PyQt'. If it's about 'Laravel', say 'I have extensive experience in Laravel'. "
        f"Here are examples of the **PERFECT** style:\n"
        f"- 'Hi, I’m BlueLion, expert in AI/ML and data migration. I can convert XML to PySpark with schema checks, filtering, and metadata. I use smart AI to make the process fast and clean. Check my work: {PORTFOLIO_URL}.'\n"
        f"- 'Hi, I’m BlueLion, expert in API integration. I can connect your custom API with DUDA and set real-time updates from Printify. I know DUDA and Printify well. See my work: {PORTFOLIO_URL}.'\n"
        f"**Rules:** Start with 'Hi, I'm {USERNAME},...'. Mention the portfolio: {PORTFOLIO_URL}. Do NOT use formal closings.\n\n"
        f"--- YOUR RESPONSE FORMAT ---\n"
        f"RATING: [Your rating word]\n"
        f"---\n"
        f"SUMMARY: [Your conversational, insightful summary]\n"
        f"---\n"
        f"BID: [Your hyper-specific, confident proposal]"
    )

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system",
                 "content": "You are a confident, expert freelance assistant named BlueLion. You write hyper-specific, compelling bids and summaries."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=400,  # Немного увеличим лимит для более качественных ответов
            temperature=0.7
        )
        full_response = response.choices[0].message.content.strip()

        # --- Парсинг 3-частного ответа (остается без изменений) ---
        parts = full_response.split("---")
        if len(parts) != 3:
            logging.warning(f"AI did not return 3 parts. Response: {full_response}")
            return "Unknown", "Could not parse AI summary.", full_response

        rating = parts[0].replace("RATING:", "").strip()
        summary = parts[1].replace("SUMMARY:", "").strip()
        bid_text = parts[2].replace("BID:", "").strip()

        logging.info(f"AI summary received. Rating: {rating}")
        return rating, summary, bid_text

    except Exception as e:
        logging.error(f"Error calling OpenAI API for summary: {e}")
        return "Error", "AI Error", "Could not generate AI summary."