"""Account configuration — represents one Freelancer account's settings."""

from dataclasses import dataclass, field
from typing import List
from dotenv import dotenv_values


def _split_csv(raw: str) -> List[str]:
    """Split comma-separated string into stripped non-empty items."""
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class AccountConfig:
    """One account's full configuration loaded from an .env file."""

    # Identity
    name: str                          # e.g. "ymka", "yehia"
    env_path: str                      # path to .env file

    # API tokens
    freelancer_token: str
    freelancer_auth_v2: str = ""
    telegram_token: str = ""
    telegram_chat_ids: List[str] = field(default_factory=list)

    # Telegram topic (for forum groups)
    telegram_thread_id: int = 0  # message_thread_id for topic-based groups (0 = no topic)

    # Gemini
    gemini_model: str = "gemini-3-flash-preview"
    bid_model: str = "gemini-3-flash-preview"
    gemini_pool_model: str = "gemini-3-flash-preview"
    bid_pool_model: str = "gemini-3-flash-preview"
    gemini_overload_fallback: str = "gemini-3-flash-preview"
    gemini_home_primary: str = ""
    gemini_home_pool: List[str] = field(default_factory=list)

    # Prompts & persona
    prompts_dir: str = "prompts"
    username: str = "Freelancer"
    portfolio_url: str = ""

    # Filters (from .env — static)
    skill_ids: List[int] = field(default_factory=list)
    blacklist_keywords: List[str] = field(default_factory=list)
    blocked_countries: List[str] = field(default_factory=list)
    allowed_countries: List[str] = field(default_factory=list)
    block_unknown_countries: bool = True
    blocked_currencies: List[str] = field(default_factory=list)
    allowed_languages: List[str] = field(default_factory=lambda: ["en"])
    verification_keywords: List[str] = field(default_factory=list)

    # Defaults (overridable via runtime_settings in DB)
    max_project_age_hours: float = 2.0
    max_bid_count: int = 100
    min_daily_rate: int = 100
    default_bid_period: int = 3
    default_milestone_pct: int = 100
    ai_request_delay: int = 15

    # Misc
    log_level: str = "INFO"
    github_token: str = ""
    github_repo: str = ""


def load_account(env_path: str) -> AccountConfig:
    """Load an AccountConfig from an .env file.

    The account name is derived from the filename:
    .env.ymka → "ymka", .env.yehia → "yehia"
    """
    vals = dotenv_values(env_path)

    def get(key: str, default: str = "") -> str:
        return vals.get(key, default)

    # Derive account name from filename: .env.ymka → ymka
    from pathlib import Path
    filename = Path(env_path).name  # e.g. ".env.ymka"
    name = filename.split(".", 2)[-1] if filename.count(".") >= 2 else filename

    return AccountConfig(
        name=name,
        env_path=env_path,
        # Tokens
        freelancer_token=get("FREELANCER_OAUTH_TOKEN"),
        freelancer_auth_v2=get("FREELANCER_AUTH_V2"),
        telegram_token=get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_ids=_split_csv(get("TELEGRAM_CHAT_IDS")),
        telegram_thread_id=int(get("TELEGRAM_THREAD_ID", "0")),
        # Gemini
        gemini_model=get("GEMINI_MODEL", "gemini-3.1-pro-preview"),
        bid_model=get("BID_MODEL", "gemini-3-flash-preview"),
        gemini_pool_model=get("GEMINI_POOL_MODEL", "gemini-3-flash-preview"),
        bid_pool_model=get("BID_POOL_MODEL", "gemini-3-flash-preview"),
        gemini_overload_fallback=get("GEMINI_OVERLOAD_FALLBACK_MODEL", "gemini-3-flash-preview"),
        gemini_home_primary=get("GEMINI_HOME_PRIMARY"),
        gemini_home_pool=_split_csv(get("GEMINI_HOME_POOL")),
        # Persona
        prompts_dir=get("PROMPTS_DIR", "prompts"),
        username=get("USERNAME", "Freelancer"),
        portfolio_url=get("PORTFOLIO_URL"),
        # Filters
        skill_ids=[int(x) for x in _split_csv(get("SKILL_IDS")) if x.isdigit()],
        blacklist_keywords=[w.lower() for w in _split_csv(get("BL"))],
        blocked_countries=[c.lower() for c in _split_csv(get("BLOCKED_COUNTRIES"))],
        allowed_countries=[c.lower() for c in _split_csv(get("ALLOWED_COUNTRIES"))],
        block_unknown_countries=get("BLOCK_UNKNOWN_COUNTRIES", "true").lower() == "true",
        blocked_currencies=[c.upper() for c in _split_csv(get("BLOCKED_CURRENCIES"))],
        allowed_languages=[l.lower() for l in _split_csv(get("LANGUAGES", "en"))],
        verification_keywords=[k.lower() for k in _split_csv(get("VERIFICATION_KEYWORDS", "cryptocurrency,crypto,bitcoin,blockchain,nft,web3"))],
        # Defaults
        max_project_age_hours=float(get("MAX_PROJECT_AGE_HOURS", "2.0")),
        max_bid_count=int(get("MAX_BID_COUNT", "100")),
        min_daily_rate=int(get("MIN_DAILY_RATE", "100")),
        default_bid_period=int(get("DEFAULT_BID_PERIOD", "3")),
        default_milestone_pct=int(get("DEFAULT_MILESTONE_PCT", "100")),
        ai_request_delay=int(get("AI_REQUEST_DELAY", "15")),
        # Misc
        log_level=get("LOG_LEVEL", "INFO"),
        github_token=get("GITHUB_TOKEN"),
        github_repo=get("GITHUB_REPO"),
    )
