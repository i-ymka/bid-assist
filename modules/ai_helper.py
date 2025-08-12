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


def get_ai_summary(title: str, description: str, budget_min: float, budget_max: float) -> Tuple[str, str, str]:
    """
    ОДНИМ запросом к LLM получает рейтинг, УМНОЕ резюме и ГИПЕР-СПЕЦИФИЧНЫЙ отклик,
    учитывая скрытую сложность и бюджет.
    """
    if not client:
        return ("N/A", "AI is not configured.", "AI is not configured.")

    safe_description = description if description else ""
    trimmed_description = safe_description[:3000]  # Максимальный контекст

    # --- УЛЬТИМАТИВНЫЙ ПРОМПТ v6.0 ---
    prompt = (
        f"You are my expert freelance assistant. Your job is to analyze projects with extreme skepticism. Find hidden complexities and mismatches between budget and scope. Use simple, direct English.\n\n"
        f"**Project Details:**\n"
        f"- Title: {title}\n"
        f"- Description: {trimmed_description}\n"
        f"- Budget: ${budget_min} - ${budget_max} USD\n\n"
        f"--- TASK 1: Deep Difficulty Analysis ---\n"
        f"Analyze the project's TRUE complexity. Look for red flags like multi-threading, CAPTCHA, anti-detection, proxy integration, session management, or resume logic. Also, consider if the budget is ridiculously low for the requested work. "
        f"Rate the difficulty as EASY, MEDIUM, or HARD based on this deep analysis, not just keywords.\n\n"
        f"--- TASK 2: Insightful Summary ---\n"
        f"Explain the project's real goal in a conversational tone. AVOID robotic phrases. Mention if it's a simple script or a complex industrial-grade bot. Example: 'Okay, so this client needs a full-scale bot for mass-registering accounts on FIFA.com, including advanced anti-detection features.'\n\n"
        f"--- TASK 3: Hyper-Specific Bid Proposal ---\n"
        f"Write a 2-3 sentence bid proposal. It MUST be confident and directly reference key technologies (e.g., 'Selenium', 'IMAP', 'multi-threading'). "
        f"**If the budget is insultingly low for a HARD project, the bid should politely address this.** "
        f"Example for low-budget HARD project: 'Hi, I'm {USERNAME}. This is a complex project involving multi-threading and advanced automation. The listed budget of ${budget_max} would cover a basic proof-of-concept, but the full implementation would require a budget closer to $XXXX. See my work at {PORTFOLIO_URL}.'\n"
        f"For normally priced projects, use this style: 'Hi, I’m {USERNAME}, an expert in API integration. I can connect your custom API with DUDA. See my work: {PORTFOLIO_URL}.'\n\n"
        f"--- YOUR RESPONSE FORMAT ---\n"
        f"RATING: [Your rating word]\n"
        f"---\n"
        f"SUMMARY: [Your insightful, conversational summary]\n"
        f"---\n"
        f"BID: [Your hyper-specific, budget-aware proposal]"
    )

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system",
                 "content": "You are a skeptical, highly experienced freelance developer who spots low budgets and hidden complexities. You follow output formats perfectly."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.5  # Снижаем температуру для более аналитических ответов
        )
        full_response = response.choices[0].message.content.strip()

        # ... (Парсинг остается без изменений) ...
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