"""Tests for project filters."""

import pytest
from src.models import Project
from src.filters import SkillFilter, BudgetFilter, BlacklistFilter, FilterPipeline


@pytest.fixture
def sample_project():
    """Create a sample project for testing."""
    return Project(
        id=1,
        title="Python Bot Development",
        description="Need a bot for data processing",
        budget={"minimum": 50, "maximum": 150},
        currency={"code": "USD"},
        jobs=[{"id": 13, "name": "Python"}, {"id": 95, "name": "Web Scraping"}],
    )


class TestSkillFilter:
    def test_passes_with_matching_skill(self, sample_project):
        filter_ = SkillFilter(required_skill_ids={13, 100, 200})
        assert filter_.passes(sample_project) is True

    def test_fails_without_matching_skill(self, sample_project):
        filter_ = SkillFilter(required_skill_ids={100, 200, 300})
        assert filter_.passes(sample_project) is False

    def test_passes_with_empty_requirements(self, sample_project):
        filter_ = SkillFilter(required_skill_ids=set())
        assert filter_.passes(sample_project) is True


class TestBudgetFilter:
    def test_passes_within_range(self, sample_project):
        filter_ = BudgetFilter(min_budget=20, max_budget=200)
        assert filter_.passes(sample_project) is True

    def test_fails_below_range(self, sample_project):
        filter_ = BudgetFilter(min_budget=200, max_budget=500)
        assert filter_.passes(sample_project) is False

    def test_fails_above_range(self, sample_project):
        filter_ = BudgetFilter(min_budget=10, max_budget=100)
        assert filter_.passes(sample_project) is False


class TestBlacklistFilter:
    def test_passes_without_blacklisted_words(self, sample_project):
        filter_ = BlacklistFilter(blacklist_keywords=["crypto", "forex"])
        assert filter_.passes(sample_project) is True

    def test_fails_with_blacklisted_word_in_title(self):
        project = Project(
            id=2,
            title="Crypto Trading Bot",
            description="Some description",
            budget={"minimum": 50, "maximum": 150},
        )
        filter_ = BlacklistFilter(blacklist_keywords=["crypto", "forex"])
        assert filter_.passes(project) is False

    def test_fails_with_blacklisted_word_in_description(self):
        project = Project(
            id=3,
            title="Trading Bot",
            description="Build a forex trading system",
            budget={"minimum": 50, "maximum": 150},
        )
        filter_ = BlacklistFilter(blacklist_keywords=["crypto", "forex"])
        assert filter_.passes(project) is False


class TestFilterPipeline:
    def test_passes_all_filters(self, sample_project):
        pipeline = FilterPipeline([
            SkillFilter(required_skill_ids={13}),
            BudgetFilter(min_budget=20, max_budget=200),
            BlacklistFilter(blacklist_keywords=["crypto"]),
        ])
        assert pipeline.passes(sample_project) is True

    def test_fails_if_any_filter_fails(self, sample_project):
        pipeline = FilterPipeline([
            SkillFilter(required_skill_ids={13}),
            BudgetFilter(min_budget=200, max_budget=500),  # Will fail
            BlacklistFilter(blacklist_keywords=["crypto"]),
        ])
        assert pipeline.passes(sample_project) is False
