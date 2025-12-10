"""Application settings using Pydantic for validation."""

from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application configuration with environment variable loading and validation."""

    # API Keys & Tokens
    freelancer_oauth_token: str = Field(..., alias="FREELANCER_OAUTH_TOKEN")
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    openai_api_key: str = Field(..., alias="OPENAI_API_KEY")

    # Telegram Settings
    telegram_chat_ids_raw: str = Field("", alias="TELEGRAM_CHAT_IDS")

    # LLM Settings
    llm_model: str = Field("gpt-4o-mini", alias="LLM_MODEL")

    # Filtering Settings
    blacklist_raw: str = Field("", alias="BL")
    skill_ids_raw: str = Field("", alias="SKILL_IDS")
    min_budget: int = Field(20, alias="MIN_BUDGET")
    max_budget: int = Field(250, alias="MAX_BUDGET")

    # Application Settings
    poll_interval: int = Field(300, alias="POLL_INTERVAL")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # User Details
    username: str = Field("Freelancer", alias="USERNAME")
    portfolio_url: str = Field("", alias="PORTFOLIO_URL")

    # Bid Settings
    default_bid_period: int = Field(3, alias="DEFAULT_BID_PERIOD")
    default_milestone_pct: int = Field(100, alias="DEFAULT_MILESTONE_PCT")

    # Database
    db_path: str = Field("processed_projects.db", alias="DB_PATH")

    class Config:
        env_file = ".env"
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


# Singleton settings instance
settings = Settings()
