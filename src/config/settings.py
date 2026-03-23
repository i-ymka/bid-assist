"""Application settings using Pydantic for validation."""

import os
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field

_env_file = os.getenv("ENV_FILE", ".env")


class Settings(BaseSettings):
    """Application configuration with environment variable loading and validation."""

    # API Keys & Tokens
    freelancer_oauth_token: str = Field(..., alias="FREELANCER_OAUTH_TOKEN")
    freelancer_auth_v2: str = Field("", alias="FREELANCER_AUTH_V2")
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    # Telegram Settings
    telegram_chat_ids_raw: str = Field("", alias="TELEGRAM_CHAT_IDS")

    # Gemini CLI models
    gemini_model: str = Field("gemini-3.1-pro-preview", alias="GEMINI_MODEL")      # Call 1: primary (pro account)
    bid_model: str = Field("gemini-3.1-flash-lite-preview", alias="BID_MODEL")     # Call 2: primary (pro account)
    gemini_pool_model: str = Field("gemini-3-pro-preview", alias="GEMINI_POOL_MODEL")    # Call 1: pool (free accounts)
    bid_pool_model: str = Field("gemini-3-flash-preview", alias="BID_POOL_MODEL")       # Call 2: pool (free accounts)

    # Gemini account pool (multi-account quota rotation)
    gemini_home_primary: str = Field("", alias="GEMINI_HOME_PRIMARY")   # path for pro account home dir (empty = default ~/.gemini)
    gemini_home_pool_raw: str = Field("", alias="GEMINI_HOME_POOL")     # comma-separated paths for free account home dirs

    @property
    def gemini_home_pool(self) -> List[str]:
        """Parse comma-separated pool account home directories."""
        return [p.strip() for p in self.gemini_home_pool_raw.split(",") if p.strip()]

    # Filtering Settings
    blacklist_raw: str = Field("", alias="BL")
    skill_ids_raw: str = Field("", alias="SKILL_IDS")
    allowed_countries_raw: str = Field("", alias="ALLOWED_COUNTRIES")
    blocked_countries_raw: str = Field("", alias="BLOCKED_COUNTRIES")
    block_unknown_countries: bool = Field(True, alias="BLOCK_UNKNOWN_COUNTRIES")
    blocked_currencies_raw: str = Field("", alias="BLOCKED_CURRENCIES")
    languages_raw: str = Field("en", alias="LANGUAGES")
    verification_keywords_raw: str = Field(
        "cryptocurrency,crypto,bitcoin,blockchain,nft,web3",
        alias="VERIFICATION_KEYWORDS"
    )

    # Application Settings
    # Note: budget, verified_account, skip_preferred_only, poll_interval are configured in bot via /settings
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    max_project_age_hours: float = Field(2.0, alias="MAX_PROJECT_AGE_HOURS")
    ai_request_delay: int = Field(15, alias="AI_REQUEST_DELAY")
    max_bid_count: int = Field(100, alias="MAX_BID_COUNT")
    reset_on_start: bool = Field(False, alias="RESET_ON_START")

    # User Details
    username: str = Field("Freelancer", alias="USERNAME")
    portfolio_url: str = Field("", alias="PORTFOLIO_URL")

    # Bid Settings
    default_bid_period: int = Field(3, alias="DEFAULT_BID_PERIOD")
    default_milestone_pct: int = Field(100, alias="DEFAULT_MILESTONE_PCT")
    min_daily_rate: int = Field(100, alias="MIN_DAILY_RATE")

    # GitHub Integration
    github_token: str = Field("", alias="GITHUB_TOKEN")
    github_repo: str = Field("", alias="GITHUB_REPO")

    # Database
    db_path: str = Field("data/processed_projects.db", alias="DB_PATH")

    # Prompts directory (set per-account to use different persona/prompts)
    prompts_dir: str = Field("prompts", alias="PROMPTS_DIR")

    class Config:
        env_file = _env_file
        env_file_encoding = "utf-8"
        extra = "ignore"

    @property
    def telegram_chat_ids(self) -> List[str]:
        """Parse comma-separated chat IDs into a list."""
        return [
            chat_id.strip()
            for chat_id in self.telegram_chat_ids_raw.split(",")
            if chat_id.strip()
        ]

    @property
    def blacklist_keywords(self) -> List[str]:
        """Parse comma-separated blacklist keywords into a list."""
        return [
            word.strip().lower()
            for word in self.blacklist_raw.split(",")
            if word.strip()
        ]

    @property
    def skill_ids(self) -> List[int]:
        """Parse comma-separated skill IDs into a list of integers."""
        return [
            int(skill_id.strip())
            for skill_id in self.skill_ids_raw.split(",")
            if skill_id.strip()
        ]

    @property
    def allowed_countries(self) -> List[str]:
        """Parse comma-separated allowed countries into a list (whitelist)."""
        return [
            country.strip().lower()
            for country in self.allowed_countries_raw.split(",")
            if country.strip()
        ]

    @property
    def blocked_countries(self) -> List[str]:
        """Parse comma-separated blocked countries into a list (blacklist)."""
        return [
            country.strip().lower()
            for country in self.blocked_countries_raw.split(",")
            if country.strip()
        ]

    @property
    def blocked_currencies(self) -> List[str]:
        """Parse comma-separated blocked currency codes into a list (e.g., INR, PKR)."""
        return [
            currency.strip().upper()
            for currency in self.blocked_currencies_raw.split(",")
            if currency.strip()
        ]

    @property
    def allowed_languages(self) -> List[str]:
        """Parse comma-separated language codes into a list."""
        return [
            lang.strip().lower()
            for lang in self.languages_raw.split(",")
            if lang.strip()
        ]

    @property
    def verification_keywords(self) -> List[str]:
        """Parse comma-separated verification keywords into a list."""
        return [
            kw.strip().lower()
            for kw in self.verification_keywords_raw.split(",")
            if kw.strip()
        ]


# Singleton settings instance
settings = Settings()
