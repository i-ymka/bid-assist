"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_project_data():
    """Sample project data from Freelancer API."""
    return {
        "id": 12345,
        "title": "Python Web Scraping Bot",
        "description": "Need a Python script to scrape data from websites.",
        "budget": {"minimum": 50, "maximum": 150},
        "currency": {"code": "USD", "name": "US Dollar"},
        "owner": {"id": 1, "username": "client123"},
        "jobs": [
            {"id": 13, "name": "Python"},
            {"id": 95, "name": "Web Scraping"},
        ],
        "status": "active",
        "type": "fixed",
    }


@pytest.fixture
def mock_settings(monkeypatch):
    """Mock settings for testing."""
    monkeypatch.setenv("FREELANCER_OAUTH_TOKEN", "test_token")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_bot_token")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "123456")
    monkeypatch.setenv("OPENAI_API_KEY", "test_openai_key")
    monkeypatch.setenv("SKILL_IDS", "13,95,116")
    monkeypatch.setenv("MIN_BUDGET", "20")
    monkeypatch.setenv("MAX_BUDGET", "250")
    monkeypatch.setenv("BL", "crypto,forex")
