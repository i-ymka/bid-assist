# config.py (версия для мульти-ID, без YAML)
import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys & Tokens ---
FREELANCER_OAUTH_TOKEN = os.getenv("FREELANCER_OAUTH_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- ИЗМЕНЕНИЕ ЗДЕСЬ ---
# Превращаем строку "id1,id2" в список ['id1', 'id2']
TELEGRAM_CHAT_IDS = [chat_id.strip() for chat_id in os.getenv("TELEGRAM_CHAT_IDS", "").split(',') if chat_id.strip()]
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# --- LLM Settings ---
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# ... (остальной код остается таким же, как в вашей работающей версии)

# --- Filtering Settings ---
WL_KEYWORDS = [word.strip().lower() for word in os.getenv("WL", "").split(',') if word.strip()]
BL_KEYWORDS = [word.strip().lower() for word in os.getenv("BL", "").split(',') if word.strip()]
MIN_BUDGET = int(os.getenv("MIN_BUDGET", 20))
MAX_BUDGET = int(os.getenv("MAX_BUDGET", 150))
SKILL_IDS = [int(skill_id.strip()) for skill_id in os.getenv("SKILL_IDS", "").split(',') if skill_id.strip()]

# --- Application Settings ---
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 300))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- User Details ---
USERNAME = os.getenv("USERNAME", "Freelancer")
PORTFOLIO_URL = os.getenv("PORTFOLIO_URL", "")

# --- Проверка обязательных переменных ---
REQUIRED_VARS = [
    "FREELANCER_OAUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_IDS", # <--- Заменили здесь
    "OPENAI_API_KEY",
]

missing_vars = [var for var in REQUIRED_VARS if not globals()[var]]
if missing_vars:
    raise ValueError(f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}")