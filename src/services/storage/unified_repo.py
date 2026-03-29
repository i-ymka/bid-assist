"""Unified SQLite repository for the orchestrator.

Single database replaces per-account DBs + shared_analysis.db.
All tables use account-aware design: either an `account` column
or the `project_accounts` junction table for tagging.
"""

import sqlite3
import logging
import threading
from pathlib import Path
from typing import Optional, List

logger = logging.getLogger(__name__)


class UnifiedRepo:
    """Single database for all accounts managed by the orchestrator."""

    def __init__(self, db_path: str = "data/orchestrator.db"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._connect()
        self._create_tables()

    def _connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def _create_tables(self):
        with self._conn:
            # ── Projects: replaces processed_projects + project_queue + shared_analysis ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id      INTEGER PRIMARY KEY,
                    title           TEXT,
                    description     TEXT,
                    budget_min      REAL,
                    budget_max      REAL,
                    currency        TEXT DEFAULT 'USD',
                    client_country  TEXT,
                    bid_count       INTEGER,
                    avg_bid         REAL,
                    url             TEXT,
                    skill_names     TEXT,
                    owner_username  TEXT DEFAULT '',
                    owner_display_name TEXT DEFAULT '',
                    is_preferred_only INTEGER DEFAULT 0,
                    time_submitted  TIMESTAMP,
                    fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status          TEXT DEFAULT 'pending',
                    call1_verdict   TEXT,
                    call1_days      INTEGER,
                    call1_summary   TEXT
                )
            """)

            # ── Tags: which accounts are interested in which projects ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS project_accounts (
                    project_id   INTEGER NOT NULL,
                    account      TEXT NOT NULL,
                    price_ok     INTEGER DEFAULT 1,
                    bid_placed   INTEGER DEFAULT 0,
                    bid_id       INTEGER,
                    PRIMARY KEY (project_id, account)
                )
            """)

            # ── Bid history ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bid_history (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id      INTEGER NOT NULL,
                    account         TEXT NOT NULL,
                    amount          REAL NOT NULL,
                    period          INTEGER NOT NULL,
                    description     TEXT,
                    success         INTEGER NOT NULL,
                    error_message   TEXT,
                    title           TEXT,
                    summary         TEXT,
                    url             TEXT,
                    currency        TEXT DEFAULT 'USD',
                    bid_count       INTEGER,
                    budget_min      REAL,
                    budget_max      REAL,
                    client_country  TEXT,
                    avg_bid         REAL,
                    notification_sent INTEGER DEFAULT 0,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Pending bids (staging for Telegram buttons) ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_bids (
                    project_id      INTEGER NOT NULL,
                    account         TEXT NOT NULL,
                    amount          REAL NOT NULL,
                    period          INTEGER NOT NULL,
                    description     TEXT,
                    title           TEXT,
                    summary         TEXT,
                    currency        TEXT DEFAULT 'USD',
                    url             TEXT,
                    bid_count       INTEGER DEFAULT 0,
                    budget_min      REAL,
                    budget_max      REAL,
                    client_country  TEXT,
                    avg_bid         REAL,
                    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (project_id, account)
                )
            """)

            # ── Bid outcomes (win/loss cache) ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS bid_outcomes (
                    project_id          INTEGER NOT NULL,
                    account             TEXT NOT NULL,
                    outcome             TEXT NOT NULL,
                    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                    winner_amount       REAL,
                    winner_proposal_len INTEGER,
                    winner_proposal     TEXT,
                    winner_reviews      INTEGER,
                    winner_hourly_rate  REAL,
                    winner_reg_date     INTEGER,
                    winner_earnings_score REAL,
                    winner_portfolio_count INTEGER,
                    my_time_to_bid_sec  INTEGER,
                    winner_time_to_bid_sec INTEGER,
                    PRIMARY KEY (project_id, account)
                )
            """)

            # ── Runtime settings: per-account, key = "account:setting" ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL,
                    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # ── Project colors (round-robin for terminal output) ──
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS project_colors (
                    project_id  INTEGER PRIMARY KEY,
                    color_index INTEGER NOT NULL
                )
            """)

        logger.debug("Unified database tables initialized")

    # ──────────────────────────────────────────────────────────────────
    # Projects
    # ──────────────────────────────────────────────────────────────────

    def add_project(self, project_id: int, **kwargs) -> bool:
        """Insert a new project. Returns False if already exists."""
        try:
            cols = ["project_id"] + list(kwargs.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_str = ", ".join(cols)
            with self._lock, self._conn:
                self._conn.execute(
                    f"INSERT OR IGNORE INTO projects ({col_str}) VALUES ({placeholders})",
                    [project_id] + list(kwargs.values()),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"add_project({project_id}): {e}")
            return False

    def is_known(self, project_id: int) -> bool:
        """Check if project already exists in DB (any status)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        return row is not None

    def get_pending_projects(self, limit: int = 10) -> List[dict]:
        """Get projects waiting for Call 1 analysis."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE status = 'pending' ORDER BY fetched_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_done_projects(self, limit: int = 10) -> List[dict]:
        """Get projects that finished Call 1 with verdict PASS, awaiting Call 2."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM projects WHERE status = 'done' AND call1_verdict = 'PASS' ORDER BY fetched_at LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_status(self, project_id: int, status: str) -> bool:
        """Update project status: pending, analyzing, done, skipped, bidded."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE projects SET status = ? WHERE project_id = ?",
                    (status, project_id),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"set_status({project_id}, {status}): {e}")
            return False

    def store_call1(self, project_id: int, verdict: str, days: int, summary: str):
        """Store Call 1 result and set status to done/skipped."""
        status = "skipped" if verdict == "SKIP" else "done"
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """UPDATE projects
                       SET call1_verdict = ?, call1_days = ?, call1_summary = ?, status = ?
                       WHERE project_id = ?""",
                    (verdict, days, summary, status, project_id),
                )
        except sqlite3.Error as e:
            logger.error(f"store_call1({project_id}): {e}")

    def get_project(self, project_id: int) -> Optional[dict]:
        """Get full project data."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        return dict(row) if row else None

    # ──────────────────────────────────────────────────────────────────
    # Tags (project_accounts)
    # ──────────────────────────────────────────────────────────────────

    def tag_project(self, project_id: int, account: str) -> bool:
        """Add an account tag to a project."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO project_accounts (project_id, account) VALUES (?, ?)",
                    (project_id, account),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"tag_project({project_id}, {account}): {e}")
            return False

    def remove_tag(self, project_id: int, account: str):
        """Remove an account tag (e.g. price check failed)."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM project_accounts WHERE project_id = ? AND account = ?",
                    (project_id, account),
                )
        except sqlite3.Error as e:
            logger.error(f"remove_tag({project_id}, {account}): {e}")

    def get_tags(self, project_id: int) -> List[str]:
        """Get all account names tagged for a project."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT account FROM project_accounts WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return [r["account"] for r in rows]

    def get_unbid_tags(self, project_id: int) -> List[str]:
        """Get account names tagged but not yet bid for a project."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT account FROM project_accounts WHERE project_id = ? AND bid_placed = 0 AND price_ok = 1",
                (project_id,),
            ).fetchall()
        return [r["account"] for r in rows]

    def mark_price_fail(self, project_id: int, account: str):
        """Mark that price check failed for this account on this project."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE project_accounts SET price_ok = 0 WHERE project_id = ? AND account = ?",
                    (project_id, account),
                )
        except sqlite3.Error as e:
            logger.error(f"mark_price_fail({project_id}, {account}): {e}")

    def mark_bid_placed(self, project_id: int, account: str, bid_id: int = None):
        """Record that bid was placed for this account on this project."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "UPDATE project_accounts SET bid_placed = 1, bid_id = ? WHERE project_id = ? AND account = ?",
                    (bid_id, project_id, account),
                )
        except sqlite3.Error as e:
            logger.error(f"mark_bid_placed({project_id}, {account}): {e}")

    # ──────────────────────────────────────────────────────────────────
    # Runtime settings (per-account)
    # ──────────────────────────────────────────────────────────────────

    def _skey(self, account: str, key: str) -> str:
        """Build settings key: 'ymka:budget_min'."""
        return f"{account}:{key}"

    def get_setting(self, account: str, key: str, default: str = "") -> str:
        """Get a per-account runtime setting."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT value FROM runtime_settings WHERE key = ?",
                    (self._skey(account, key),),
                ).fetchone()
            return row["value"] if row else default
        except sqlite3.Error:
            return default

    def set_setting(self, account: str, key: str, value: str) -> bool:
        """Set a per-account runtime setting."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO runtime_settings (key, value, updated_at) "
                    "VALUES (?, ?, CURRENT_TIMESTAMP)",
                    (self._skey(account, key), value),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"set_setting({account}:{key}): {e}")
            return False

    def init_account_defaults(self, account: str, defaults: dict):
        """Seed default settings for an account (INSERT OR IGNORE — won't overwrite)."""
        try:
            with self._lock, self._conn:
                for key, value in defaults.items():
                    self._conn.execute(
                        "INSERT OR IGNORE INTO runtime_settings (key, value) VALUES (?, ?)",
                        (self._skey(account, key), str(value)),
                    )
        except sqlite3.Error as e:
            logger.error(f"init_account_defaults({account}): {e}")

    # Typed setting helpers
    def is_paused(self, account: str) -> bool:
        return self.get_setting(account, "paused", "false") == "true"

    def set_paused(self, account: str, paused: bool) -> bool:
        return self.set_setting(account, "paused", str(paused).lower())

    def get_budget_range(self, account: str) -> tuple:
        mn = int(self.get_setting(account, "budget_min", "50"))
        mx = int(self.get_setting(account, "budget_max", "1000"))
        return (mn, mx)

    def set_budget_range(self, account: str, min_b: int, max_b: int) -> bool:
        return (
            self.set_setting(account, "budget_min", str(min_b))
            and self.set_setting(account, "budget_max", str(max_b))
        )

    def get_poll_interval(self, account: str) -> int:
        return int(self.get_setting(account, "poll_interval", "300"))

    def set_poll_interval(self, account: str, seconds: int) -> bool:
        return self.set_setting(account, "poll_interval", str(seconds))

    def get_min_daily_rate(self, account: str) -> int:
        return int(self.get_setting(account, "min_daily_rate", "100"))

    def set_min_daily_rate(self, account: str, rate: int) -> bool:
        return self.set_setting(account, "min_daily_rate", str(rate))

    def get_max_bid_count(self, account: str) -> int:
        return int(self.get_setting(account, "max_bid_count", "100"))

    def set_max_bid_count(self, account: str, count: int) -> bool:
        return self.set_setting(account, "max_bid_count", str(count))

    def get_bid_adjustment(self, account: str) -> int:
        return int(self.get_setting(account, "bid_adjustment", "-10"))

    def set_bid_adjustment(self, account: str, pct: int) -> bool:
        return self.set_setting(account, "bid_adjustment", str(pct))

    def get_rate_tier2_pct(self, account: str) -> int:
        return int(self.get_setting(account, "rate_tier2_pct", "65"))

    def set_rate_tier2_pct(self, account: str, pct: int) -> bool:
        return self.set_setting(account, "rate_tier2_pct", str(pct))

    def get_rate_tier3_pct(self, account: str) -> int:
        return int(self.get_setting(account, "rate_tier3_pct", "50"))

    def set_rate_tier3_pct(self, account: str, pct: int) -> bool:
        return self.set_setting(account, "rate_tier3_pct", str(pct))

    def is_verified(self, account: str) -> bool:
        return self.get_setting(account, "verified", "true") == "true"

    def set_verified(self, account: str, verified: bool) -> bool:
        return self.set_setting(account, "verified", str(verified).lower())

    def skip_preferred_only(self, account: str) -> bool:
        return self.get_setting(account, "skip_preferred_only", "true") == "true"

    def set_skip_preferred_only(self, account: str, skip: bool) -> bool:
        return self.set_setting(account, "skip_preferred_only", str(skip).lower())

    def is_auto_bid(self, account: str) -> bool:
        return self.get_setting(account, "auto_bid", "true") == "true"

    def set_auto_bid(self, account: str, enabled: bool) -> bool:
        return self.set_setting(account, "auto_bid", str(enabled).lower())

    def get_notif_mode(self, account: str) -> str:
        mode = self.get_setting(account, "notif_mode", "all")
        return mode if mode in ("all", "bids_plus", "bids") else "all"

    def set_notif_mode(self, account: str, mode: str) -> bool:
        return self.set_setting(account, "notif_mode", mode)

    def get_max_project_age(self, account: str) -> float:
        return float(self.get_setting(account, "max_project_age", "2.0"))

    def get_bid_delay(self, account: str) -> int:
        return int(self.get_setting(account, "bid_delay", "5"))

    def set_bid_delay(self, account: str, minutes: int) -> bool:
        return self.set_setting(account, "bid_delay", str(minutes))

    # ──────────────────────────────────────────────────────────────────
    # Bid history
    # ──────────────────────────────────────────────────────────────────

    def add_bid_record(self, account: str, project_id: int, amount: float,
                       period: int, description: str, success: bool,
                       error_message: str = "", **extra) -> Optional[int]:
        """Record a bid attempt."""
        try:
            cols = ["account", "project_id", "amount", "period", "description", "success", "error_message"]
            vals = [account, project_id, amount, period, description, int(success), error_message]
            for k, v in extra.items():
                cols.append(k)
                vals.append(v)
            col_str = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    f"INSERT INTO bid_history ({col_str}) VALUES ({placeholders})", vals
                )
            return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"add_bid_record({account}, {project_id}): {e}")
            return None

    def update_bid_record_on_place(
        self,
        account: str,
        project_id: int,
        amount: float,
        period: int,
        description: str,
        success: bool,
        error_message: str = None,
        notification_sent: bool = True,
    ) -> bool:
        """Update an existing pending_manual bid record when bid is actually placed.

        If no pending_manual record exists, creates a new one.
        """
        import sqlite3 as _sqlite3
        try:
            with self._lock, self._conn:
                row = self._conn.execute(
                    "SELECT id FROM bid_history WHERE account = ? AND project_id = ? AND error_message = 'pending_manual'",
                    (account, project_id),
                ).fetchone()
                if row:
                    self._conn.execute(
                        """UPDATE bid_history
                           SET amount = ?, period = ?, description = ?,
                               success = ?, error_message = ?, notification_sent = ?
                           WHERE id = ?""",
                        (amount, period, description, int(success),
                         error_message, int(notification_sent), row["id"]),
                    )
                else:
                    self.add_bid_record(
                        account, project_id, amount, period, description, success,
                        error_message=error_message or "",
                        notification_sent=int(notification_sent),
                    )
            return True
        except Exception as e:
            logger.error(f"update_bid_record_on_place({account}, {project_id}): {e}")
            return False

    def get_recent_bids(self, account: str, limit: int = 50, since: str = None) -> List[dict]:
        """Get recent bids for an account."""
        query = "SELECT * FROM bid_history WHERE account = ? AND success = 1"
        params: list = [account]
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_recent_bids_full(self, account: str, limit: int = None, since: str = None) -> List[dict]:
        """Get recent successful bids with all stored columns (for /bidstats)."""
        conditions = ["account = ?", "success = 1"]
        params: list = [account]
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        sql = (
            "SELECT project_id, amount, period, description, created_at, "
            "title, summary, url, currency, bid_count, "
            "budget_min, budget_max, client_country, avg_bid "
            "FROM bid_history WHERE " + " AND ".join(conditions) +
            " ORDER BY created_at DESC"
        )
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_bid_stats(self, account: str, since: str = None) -> dict:
        """Get bid statistics (count + avg amount) for an account."""
        conditions = ["account = ?", "success = 1"]
        params: list = [account]
        if since:
            conditions.append("created_at >= ?")
            params.append(since)
        where = " AND ".join(conditions)
        try:
            with self._lock:
                row = self._conn.execute(
                    f"SELECT COUNT(*) as bids_placed, AVG(amount) as avg_amount "
                    f"FROM bid_history WHERE {where}",
                    params,
                ).fetchone()
            return {
                "bids_placed": row["bids_placed"] or 0,
                "avg_amount": round(row["avg_amount"] or 0, 2),
            }
        except Exception as e:
            logger.error(f"get_bid_stats({account}): {e}")
            return {"bids_placed": 0, "avg_amount": 0}

    def get_processed_count(self, account: str, since: str = None) -> int:
        """Count projects seen by this account (tagged in project_accounts)."""
        try:
            if since:
                with self._lock:
                    row = self._conn.execute(
                        "SELECT COUNT(*) FROM project_accounts pa "
                        "JOIN projects p ON pa.project_id = p.project_id "
                        "WHERE pa.account = ? AND p.fetched_at >= ?",
                        (account, since),
                    ).fetchone()
            else:
                with self._lock:
                    row = self._conn.execute(
                        "SELECT COUNT(*) FROM project_accounts WHERE account = ?",
                        (account,),
                    ).fetchone()
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"get_processed_count({account}): {e}")
            return 0

    def set_max_project_age(self, account: str, hours: float) -> bool:
        return self.set_setting(account, "max_project_age", str(hours))

    # ──────────────────────────────────────────────────────────────────
    # Pending bids (Telegram staging)
    # ──────────────────────────────────────────────────────────────────

    def add_pending_bid(self, account: str, project_id: int, **kwargs) -> bool:
        """Stage a bid for Telegram button placement."""
        try:
            cols = ["project_id", "account"] + list(kwargs.keys())
            vals = [project_id, account] + list(kwargs.values())
            col_str = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))
            with self._lock, self._conn:
                self._conn.execute(
                    f"INSERT OR REPLACE INTO pending_bids ({col_str}) VALUES ({placeholders})",
                    vals,
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"add_pending_bid({account}, {project_id}): {e}")
            return False

    def get_pending_bid(self, account: str, project_id: int) -> Optional[dict]:
        """Get staged bid data."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pending_bids WHERE project_id = ? AND account = ?",
                (project_id, account),
            ).fetchone()
        return dict(row) if row else None

    def remove_pending_bid(self, account: str, project_id: int) -> bool:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM pending_bids WHERE project_id = ? AND account = ?",
                    (project_id, account),
                )
            return True
        except sqlite3.Error:
            return False

    # ──────────────────────────────────────────────────────────────────
    # Bid outcomes
    # ──────────────────────────────────────────────────────────────────

    def set_bid_outcome(self, account: str, project_id: int, outcome: str,
                        winner_detail: dict = None):
        """Set or update bid outcome for an account's bid on a project."""
        try:
            cols = ["project_id", "account", "outcome"]
            vals = [project_id, account, outcome]
            if winner_detail:
                for k, v in winner_detail.items():
                    if v is not None:
                        cols.append(k)
                        vals.append(v)
            col_str = ", ".join(cols)
            placeholders = ", ".join(["?"] * len(cols))
            # Upsert
            update_str = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("project_id", "account"))
            with self._lock, self._conn:
                self._conn.execute(
                    f"""INSERT INTO bid_outcomes ({col_str}) VALUES ({placeholders})
                        ON CONFLICT(project_id, account) DO UPDATE SET {update_str}, updated_at = CURRENT_TIMESTAMP""",
                    vals,
                )
        except sqlite3.Error as e:
            logger.error(f"set_bid_outcome({account}, {project_id}): {e}")

    def get_bid_outcome(self, account: str, project_id: int) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM bid_outcomes WHERE project_id = ? AND account = ?",
                (project_id, account),
            ).fetchone()
        return dict(row) if row else None

    # ──────────────────────────────────────────────────────────────────
    # Colors (round-robin for terminal)
    # ──────────────────────────────────────────────────────────────────

    def get_or_assign_color(self, project_id: int, palette_size: int) -> int:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO project_colors (project_id, color_index) "
                    "VALUES (?, (SELECT COUNT(*) % ? FROM project_colors))",
                    (project_id, palette_size),
                )
                row = self._conn.execute(
                    "SELECT color_index FROM project_colors WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
            return row["color_index"] if row else 0
        except sqlite3.Error:
            return 0

    # ──────────────────────────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────────────────────────

    def cleanup_old(self, max_age_hours: float = 24) -> int:
        """Remove projects (and their tags) older than max_age_hours."""
        try:
            with self._lock, self._conn:
                # Delete tags for old projects
                self._conn.execute(
                    "DELETE FROM project_accounts WHERE project_id IN "
                    "(SELECT project_id FROM projects WHERE fetched_at < datetime('now', ? || ' hours'))",
                    (f"-{max_age_hours}",),
                )
                # Delete old projects
                cursor = self._conn.execute(
                    "DELETE FROM projects WHERE fetched_at < datetime('now', ? || ' hours')",
                    (f"-{max_age_hours}",),
                )
                # Delete old colors
                self._conn.execute(
                    "DELETE FROM project_colors WHERE project_id NOT IN (SELECT project_id FROM projects)"
                )
                removed = cursor.rowcount
                if removed:
                    logger.info(f"Cleanup: removed {removed} old projects")
                return removed
        except sqlite3.Error as e:
            logger.error(f"cleanup_old: {e}")
            return 0

    # ──────────────────────────────────────────────────────────────────
    # Misc
    # ──────────────────────────────────────────────────────────────────

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("Database connection closed")

    def __del__(self):
        self.close()
