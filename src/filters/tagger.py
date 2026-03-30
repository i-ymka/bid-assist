"""Project tagger: evaluates per-account filters and assigns account tags."""

import logging
from datetime import datetime
from typing import List, Optional

from src.config.account import AccountConfig
from src.services.storage.unified_repo import UnifiedRepo

logger = logging.getLogger(__name__)


class ProjectTagger:
    """Runs each account's filter set against a project and returns matching account names."""

    def __init__(self, accounts: List[AccountConfig], repo: UnifiedRepo):
        self._accounts = accounts
        self._repo = repo

    def tag_project(self, project: dict) -> List[str]:
        """Evaluate project against all accounts' filters.

        Args:
            project: Dict with keys matching the `projects` table columns:
                     project_id, title, description, budget_min, budget_max,
                     currency, client_country, bid_count, avg_bid,
                     time_submitted, is_preferred_only, skill_names

        Returns:
            List of account names whose filters this project passes.
            Also writes tags to project_accounts in the DB.
        """
        matched = []
        pid = project["project_id"]

        for acc in self._accounts:
            reason = self._check_filters(acc, project)
            if reason:
                logger.debug(f"Project {pid} rejected for {acc.name}: {reason}")
            else:
                matched.append(acc.name)
                self._repo.tag_project(pid, acc.name)

        return matched

    def _check_filters(self, acc: AccountConfig, p: dict) -> Optional[str]:
        """Check all filters for one account. Returns rejection reason or None if passes."""
        name = acc.name

        # 1. Budget
        budget_max = p.get("budget_max") or 0
        bmin, bmax = self._repo.get_budget_range(name)
        if budget_max and not (bmin <= budget_max <= bmax):
            return f"budget ${budget_max} not in ${bmin}-${bmax}"

        # 2. Currency
        currency = (p.get("currency") or "").upper()
        if currency and acc.blocked_currencies and currency in acc.blocked_currencies:
            return f"currency {currency} blocked"

        # 3. Language (check if project has language field)
        lang = (p.get("language") or "").lower()
        if lang and acc.allowed_languages and lang not in acc.allowed_languages:
            return f"language {lang} not allowed"

        # 4. Max bid count
        bid_count = p.get("bid_count") or 0
        max_bids = self._repo.get_max_bid_count(name)
        if bid_count > max_bids:
            return f"{bid_count} bids > limit {max_bids}"

        # 5. Project age
        time_submitted = p.get("time_submitted")
        if time_submitted:
            if isinstance(time_submitted, str):
                try:
                    time_submitted = datetime.strptime(time_submitted, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    try:
                        time_submitted = datetime.fromisoformat(time_submitted)
                    except ValueError:
                        time_submitted = None
            if time_submitted:
                age_hours = (datetime.utcnow() - time_submitted).total_seconds() / 3600
                max_age = self._repo.get_max_project_age(name)
                if age_hours > max_age:
                    return f"too old ({age_hours:.1f}h > {max_age}h)"

        # 6. Preferred-only
        if self._repo.skip_preferred_only(name) and p.get("is_preferred_only"):
            return "preferred-only"

        # 7. Blacklist keywords
        if acc.blacklist_keywords:
            text = f"{p.get('title', '')} {p.get('description', '')}".lower()
            for kw in acc.blacklist_keywords:
                if kw in text:
                    return f"blacklist: '{kw}'"

        # 8. Verification keywords
        if not self._repo.is_verified(name) and acc.verification_keywords:
            text = f"{p.get('title', '')} {p.get('description', '')} {p.get('skill_names', '')}".lower()
            for kw in acc.verification_keywords:
                if kw in text:
                    return f"requires verified: '{kw}'"

        # 9. Country
        country = (p.get("client_country") or "").lower()
        if country:
            if acc.blocked_countries and country in acc.blocked_countries:
                return f"country {country} blocked"
            if acc.allowed_countries and country not in acc.allowed_countries:
                return f"country {country} not in whitelist"
        elif acc.block_unknown_countries:
            return "unknown country blocked"

        # 10. Skills: account must have at least one skill matching the project
        if acc.skill_ids:
            skill_ids_str = p.get("skill_ids_str", "")
            if skill_ids_str:
                project_skill_ids = {int(x) for x in skill_ids_str.split(",") if x.strip().isdigit()}
                if project_skill_ids and not project_skill_ids.intersection(acc.skill_ids):
                    return "no matching skills for account"

        return None  # All filters passed
