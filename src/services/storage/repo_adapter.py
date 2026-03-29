"""Adapter: makes UnifiedRepo look like the old ProjectRepository for one account.

Handlers call repo.is_paused() → adapter calls unified_repo.is_paused(account).
Minimal changes needed in handlers.py.
"""

from typing import Optional, List, Tuple
from src.services.storage.unified_repo import UnifiedRepo


class AccountRepoAdapter:
    """Wraps UnifiedRepo, scoping all calls to one account."""

    def __init__(self, repo: UnifiedRepo, account: str):
        self._repo = repo
        self._account = account

    @property
    def account(self) -> str:
        return self._account

    # ── Projects (delegated to unified repo) ──

    def add_processed_project(self, project_id: int) -> bool:
        return self._repo.set_status(project_id, "processed")

    def is_processed(self, project_id: int) -> bool:
        p = self._repo.get_project(project_id)
        return p is not None and p.get("status") in ("processed", "skipped", "bidded")

    def is_in_queue(self, project_id: int) -> bool:
        p = self._repo.get_project(project_id)
        return p is not None and p.get("status") in ("pending", "analyzing")

    def get_project_from_queue(self, project_id: int) -> Optional[dict]:
        return self._repo.get_project(project_id)

    # ── Runtime settings ──

    def is_paused(self) -> bool:
        return self._repo.is_paused(self._account)

    def set_paused(self, paused: bool) -> bool:
        return self._repo.set_paused(self._account, paused)

    def get_budget_range(self) -> tuple:
        return self._repo.get_budget_range(self._account)

    def set_budget_range(self, min_b: int, max_b: int) -> bool:
        return self._repo.set_budget_range(self._account, min_b, max_b)

    def get_poll_interval(self) -> int:
        return self._repo.get_poll_interval(self._account)

    def set_poll_interval(self, seconds: int) -> bool:
        return self._repo.set_poll_interval(self._account, seconds)

    def get_min_daily_rate(self) -> int:
        return self._repo.get_min_daily_rate(self._account)

    def set_min_daily_rate(self, rate: int) -> bool:
        return self._repo.set_min_daily_rate(self._account, rate)

    def get_max_bid_count(self) -> int:
        return self._repo.get_max_bid_count(self._account)

    def set_max_bid_count(self, count: int) -> bool:
        return self._repo.set_max_bid_count(self._account, count)

    def get_bid_adjustment(self) -> int:
        return self._repo.get_bid_adjustment(self._account)

    def set_bid_adjustment(self, pct: int) -> bool:
        return self._repo.set_bid_adjustment(self._account, pct)

    def get_rate_tier2_pct(self) -> int:
        return self._repo.get_rate_tier2_pct(self._account)

    def set_rate_tier2_pct(self, pct: int) -> bool:
        return self._repo.set_rate_tier2_pct(self._account, pct)

    def get_rate_tier3_pct(self) -> int:
        return self._repo.get_rate_tier3_pct(self._account)

    def set_rate_tier3_pct(self, pct: int) -> bool:
        return self._repo.set_rate_tier3_pct(self._account, pct)

    def is_verified(self) -> bool:
        return self._repo.is_verified(self._account)

    def set_verified(self, verified: bool) -> bool:
        return self._repo.set_verified(self._account, verified)

    def skip_preferred_only(self) -> bool:
        return self._repo.skip_preferred_only(self._account)

    def set_skip_preferred_only(self, skip: bool) -> bool:
        return self._repo.set_skip_preferred_only(self._account, skip)

    def is_auto_bid(self) -> bool:
        return self._repo.is_auto_bid(self._account)

    def set_auto_bid(self, enabled: bool) -> bool:
        return self._repo.set_auto_bid(self._account, enabled)

    def get_notif_mode(self) -> str:
        return self._repo.get_notif_mode(self._account)

    def set_notif_mode(self, mode: str) -> bool:
        return self._repo.set_notif_mode(self._account, mode)

    def get_receive_skipped(self) -> bool:
        return self.get_notif_mode() == "all"

    def set_receive_skipped(self, enabled: bool) -> bool:
        return self.set_notif_mode("all" if enabled else "bids")

    def get_max_project_age(self) -> float:
        return self._repo.get_max_project_age(self._account)

    # ── Bid history ──

    def update_bid_record_on_place(self, project_id, amount, period, description, success,
                                    error_message=None, notification_sent=True):
        return self._repo.update_bid_record_on_place(
            self._account, project_id, amount, period, description, success,
            error_message, notification_sent,
        )

    def add_bid_record(self, project_id, amount, period, description, success, error_message="", **extra):
        return self._repo.add_bid_record(
            self._account, project_id, amount, period, description, success, error_message, **extra
        )

    def get_recent_bids(self, limit=50, since=None):
        return self._repo.get_recent_bids(self._account, limit, since)

    def get_recent_bids_full(self, limit=None, since=None):
        return self._repo.get_recent_bids_full(self._account, limit, since)

    def get_bid_stats(self, since=None) -> dict:
        return self._repo.get_bid_stats(self._account, since)

    def get_processed_count(self, since=None) -> int:
        return self._repo.get_processed_count(self._account, since)

    def set_max_project_age(self, hours: float) -> bool:
        return self._repo.set_max_project_age(self._account, hours)

    def get_bid_delay(self) -> int:
        return self._repo.get_bid_delay(self._account)

    def set_bid_delay(self, minutes: int) -> bool:
        return self._repo.set_bid_delay(self._account, minutes)

    def is_project_bidded(self, project_id: int) -> bool:
        tags = self._repo.get_tags(project_id)
        # Check if our account has bid on this project
        pa = self._repo._conn.execute(
            "SELECT bid_placed FROM project_accounts WHERE project_id = ? AND account = ?",
            (project_id, self._account),
        ).fetchone()
        return pa is not None and pa["bid_placed"] == 1

    # ── Pending bids ──

    def add_pending_bid(self, project_id, **kwargs):
        return self._repo.add_pending_bid(self._account, project_id, **kwargs)

    def get_pending_bid(self, project_id) -> Optional[dict]:
        return self._repo.get_pending_bid(self._account, project_id)

    def update_pending_bid(self, project_id, **kwargs):
        # Simple update via add (REPLACE)
        existing = self.get_pending_bid(project_id)
        if existing:
            existing.update(kwargs)
            existing.pop("created_at", None)
            existing.pop("updated_at", None)
            existing.pop("account", None)
            existing.pop("project_id", None)
            return self._repo.add_pending_bid(self._account, project_id, **existing)
        return False

    def remove_pending_bid(self, project_id) -> bool:
        return self._repo.remove_pending_bid(self._account, project_id)

    # ── Bid outcomes ──

    def set_bid_outcome(self, project_id, outcome, winner_detail=None):
        return self._repo.set_bid_outcome(self._account, project_id, outcome, winner_detail)

    def get_bid_outcome(self, project_id) -> Optional[str]:
        r = self._repo.get_bid_outcome(self._account, project_id)
        return r["outcome"] if r else None

    def get_bid_outcome_full(self, project_id) -> Optional[dict]:
        return self._repo.get_bid_outcome(self._account, project_id)

    # ── Stats (delegate with account filter) ──

    def get_remaining_bids(self):
        """Delegate to bidding_service — not repo-dependent."""
        return None  # Caller should use bidding_service directly

    # ── Colors ──

    def get_or_assign_color(self, project_id, palette_size):
        return self._repo.get_or_assign_color(project_id, palette_size)

    # ── Misc settings read from DB ──

    def get_setting(self, key, default=""):
        return self._repo.get_setting(self._account, key, default)

    def set_setting(self, key, value):
        return self._repo.set_setting(self._account, key, value)

    # Status tracking
    def set_bot_start_time(self):
        return self._repo.set_setting(self._account, "bot_start_time",
                                       __import__("datetime").datetime.utcnow().isoformat())

    def get_bot_start_time(self):
        return self._repo.get_setting(self._account, "bot_start_time", None) or None

    def set_last_poll_stats(self, found=0, filtered=0, queued=0, already_bid=0):
        import json
        stats = {"found": found, "filtered": filtered, "queued": queued, "already_bid": already_bid,
                 "timestamp": __import__("datetime").datetime.utcnow().isoformat()}
        return self._repo.set_setting(self._account, "last_poll_stats", json.dumps(stats))

    def get_last_poll_stats(self):
        import json
        raw = self._repo.get_setting(self._account, "last_poll_stats", "")
        return json.loads(raw) if raw else None

    def get_total_projects_seen(self):
        return int(self._repo.get_setting(self._account, "total_projects_seen", "0"))

    def get_queue_count(self, status=None):
        # Approximate: count projects tagged for this account with given status
        if status:
            with self._repo._lock:
                row = self._repo._conn.execute(
                    "SELECT COUNT(*) as c FROM projects p JOIN project_accounts pa ON p.project_id = pa.project_id "
                    "WHERE pa.account = ? AND p.status = ?",
                    (self._account, status),
                ).fetchone()
            return row["c"] if row else 0
        return 0

    def close(self):
        pass  # UnifiedRepo manages its own connection
