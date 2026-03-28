"""SQLite repository for tracking processed projects and bids."""

import sqlite3
import logging
from datetime import datetime
from typing import Optional, List, Tuple
from src.config import settings

logger = logging.getLogger(__name__)


class ProjectRepository:
    """Repository for managing processed projects and bid history in SQLite."""

    def __init__(self, db_path: str = None):
        """Initialize the repository.

        Args:
            db_path: Path to SQLite database file. If None, uses settings.db_path.
        """
        self.db_path = db_path or settings.db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._connect()
        self._create_tables()

    def _connect(self):
        """Establish database connection."""
        try:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            logger.debug(f"Connected to database: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database {self.db_path}: {e}")
            raise

    def _create_tables(self):
        """Create required tables if they don't exist."""
        try:
            with self._conn:
                # Processed projects table
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS processed_projects (
                        project_id INTEGER PRIMARY KEY,
                        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Bid history table
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS bid_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_id INTEGER NOT NULL,
                        amount REAL NOT NULL,
                        period INTEGER NOT NULL,
                        description TEXT,
                        success INTEGER NOT NULL,
                        error_message TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Project queue table (for Custom GPT integration)
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS project_queue (
                        project_id INTEGER PRIMARY KEY,
                        title TEXT,
                        description TEXT,
                        budget_min REAL,
                        budget_max REAL,
                        currency TEXT,
                        client_country TEXT,
                        bid_count INTEGER,
                        avg_bid REAL,
                        url TEXT,
                        time_submitted TIMESTAMP,
                        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status TEXT DEFAULT 'pending'
                    )
                """)

                # Pending bids table (shared between API and Telegram bot)
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_bids (
                        project_id INTEGER PRIMARY KEY,
                        amount REAL NOT NULL,
                        period INTEGER NOT NULL,
                        description TEXT,
                        title TEXT,
                        currency TEXT DEFAULT 'USD',
                        url TEXT,
                        bid_count INTEGER DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Add processed_at column if not exists (migration for old DBs)
                try:
                    self._conn.execute("ALTER TABLE processed_projects ADD COLUMN processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                except sqlite3.OperationalError:
                    pass

                # Add url, bid_count, updated_at, summary columns if they don't exist (migration)
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN url TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN bid_count INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN summary TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN budget_min REAL")
                except sqlite3.OperationalError:
                    pass
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN budget_max REAL")
                except sqlite3.OperationalError:
                    pass
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN client_country TEXT")
                except sqlite3.OperationalError:
                    pass
                try:
                    self._conn.execute("ALTER TABLE pending_bids ADD COLUMN avg_bid REAL")
                except sqlite3.OperationalError:
                    pass

                # Runtime settings table (shared between processes)
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS runtime_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Initialize default settings if not exists
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('paused', 'false')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('poll_interval', '300')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('total_projects_seen', '0')
                """)
                # Seed budget filter defaults (INSERT OR IGNORE — won't overwrite user's values)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('budget_min', '50')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('budget_max', '1000')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('min_daily_rate', '100')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('max_bid_count', '100')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('bid_adjustment', '-10')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('rate_tier2_pct', '65')
                """)
                self._conn.execute("""
                    INSERT OR IGNORE INTO runtime_settings (key, value)
                    VALUES ('rate_tier3_pct', '50')
                """)

                # User settings table (multi-user support)
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS user_settings (
                        chat_id TEXT PRIMARY KEY,
                        name TEXT,
                        skill_ids TEXT,
                        keywords TEXT,
                        is_active INTEGER DEFAULT 1,
                        receive_skipped INTEGER DEFAULT 1,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Add notification data columns to bid_history (migration)
                for col, coltype in [
                    ("title", "TEXT"),
                    ("summary", "TEXT"),
                    ("url", "TEXT"),
                    ("currency", "TEXT DEFAULT 'USD'"),
                    ("bid_count", "INTEGER"),
                    ("budget_min", "REAL"),
                    ("budget_max", "REAL"),
                    ("client_country", "TEXT"),
                    ("avg_bid", "REAL"),
                    ("notification_sent", "INTEGER DEFAULT 0"),
                ]:
                    try:
                        self._conn.execute(f"ALTER TABLE bid_history ADD COLUMN {col} {coltype}")
                    except sqlite3.OperationalError:
                        pass

                # Add receive_skipped column if not exists (migration)
                try:
                    self._conn.execute("ALTER TABLE user_settings ADD COLUMN receive_skipped INTEGER DEFAULT 1")
                except sqlite3.OperationalError:
                    pass  # Column already exists
                    
                # Add show_bidstats_details column if not exists (migration)
                try:
                    self._conn.execute("ALTER TABLE user_settings ADD COLUMN show_bidstats_details INTEGER DEFAULT 1")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # Add skill_names column to project_queue if not exists (migration)
                try:
                    self._conn.execute("ALTER TABLE project_queue ADD COLUMN skill_names TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # Add owner_username column to project_queue if not exists (migration)
                try:
                    self._conn.execute("ALTER TABLE project_queue ADD COLUMN owner_username TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # Add owner_display_name column to project_queue if not exists (migration)
                try:
                    self._conn.execute("ALTER TABLE project_queue ADD COLUMN owner_display_name TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # Add is_preferred_only column to project_queue if not exists (migration)
                try:
                    self._conn.execute("ALTER TABLE project_queue ADD COLUMN is_preferred_only INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # Add outcome column to bid_history for caching win/loss classification
                try:
                    self._conn.execute("ALTER TABLE bid_history ADD COLUMN outcome TEXT")
                except sqlite3.OperationalError:
                    pass  # Column already exists

                # bid_outcomes — caches outcome for ALL bids (bot + manual)
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS bid_outcomes (
                        project_id INTEGER PRIMARY KEY,
                        outcome TEXT NOT NULL,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        winner_amount REAL,
                        winner_proposal_len INTEGER,
                        winner_reviews INTEGER
                    )
                """)
                # Add winner comparison columns to existing bid_outcomes rows (migration)
                for _col, _coltype in [
                    ("winner_amount", "REAL"),
                    ("winner_proposal_len", "INTEGER"),
                    ("winner_proposal", "TEXT"),
                    ("winner_reviews", "INTEGER"),
                    ("winner_hourly_rate", "REAL"),
                    ("winner_reg_date", "INTEGER"),
                    ("winner_earnings_score", "REAL"),
                    ("winner_portfolio_count", "INTEGER"),
                    ("my_time_to_bid_sec", "INTEGER"),
                    ("winner_time_to_bid_sec", "INTEGER"),
                ]:
                    try:
                        self._conn.execute(f"ALTER TABLE bid_outcomes ADD COLUMN {_col} {_coltype}")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
            logger.debug("Database tables initialized")
        except sqlite3.Error as e:
            logger.error(f"Failed to create tables: {e}")
            raise

    def add_processed_project(self, project_id: int) -> bool:
        """Mark a project as processed.

        Args:
            project_id: The project ID to mark as processed.

        Returns:
            True if added successfully, False if already exists.
        """
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO processed_projects (project_id) VALUES (?)",
                    (project_id,),
                )
                # Increment persistent counter (survives RESET_ON_START)
                self._conn.execute("""
                    UPDATE runtime_settings
                    SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE key = 'total_projects_seen'
                """)
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to add project {project_id}: {e}")
            return False

    def is_processed(self, project_id: int) -> bool:
        """Check if a project has been processed.

        Args:
            project_id: The project ID to check.

        Returns:
            True if the project was previously processed.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT 1 FROM processed_projects WHERE project_id = ?",
                (project_id,),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Failed to check project {project_id}: {e}")
            return False

    def add_bid_record(
        self,
        project_id: int,
        amount: float,
        period: int,
        description: str,
        success: bool,
        error_message: str = None,
        title: str = None,
        summary: str = None,
        url: str = None,
        currency: str = "USD",
        bid_count: int = None,
        budget_min: float = None,
        budget_max: float = None,
        client_country: str = None,
        avg_bid: float = None,
        notification_sent: bool = False,
    ) -> bool:
        """Record a bid attempt in history.

        Returns:
            True if recorded successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO bid_history
                    (project_id, amount, period, description, success, error_message,
                     title, summary, url, currency, bid_count, budget_min, budget_max,
                     client_country, avg_bid, notification_sent)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, amount, period, description, int(success), error_message,
                     title, summary, url, currency, bid_count, budget_min, budget_max,
                     client_country, avg_bid, int(notification_sent)),
                )
            logger.debug(f"Bid record saved: project {project_id}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to record bid for project {project_id}: {e}")
            return False

    def update_bid_record_on_place(
        self,
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
        try:
            with self._conn:
                cursor = self._conn.cursor()
                cursor.execute(
                    "SELECT id FROM bid_history WHERE project_id = ? AND error_message = 'pending_manual'",
                    (project_id,),
                )
                row = cursor.fetchone()
                if row:
                    self._conn.execute(
                        """UPDATE bid_history
                           SET amount = ?, period = ?, description = ?,
                               success = ?, error_message = ?, notification_sent = ?
                           WHERE id = ?""",
                        (amount, period, description, int(success), error_message,
                         int(notification_sent), row[0]),
                    )
                else:
                    self.add_bid_record(
                        project_id=project_id, amount=amount, period=period,
                        description=description, success=success,
                        error_message=error_message, notification_sent=notification_sent,
                    )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update bid record for {project_id}: {e}")
            return False

    def mark_notification_sent(self, project_id: int) -> bool:
        """Mark notification as sent for a project in bid_history."""
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE bid_history SET notification_sent = 1 WHERE project_id = ? AND notification_sent = 0",
                    (project_id,),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to mark notification sent for {project_id}: {e}")
            return False

    def get_unsent_notifications(self) -> List[dict]:
        """Get bid records where notification was not sent.

        Returns:
            List of bid data dicts with all notification fields.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT project_id, amount, period, description, success, error_message,
                       title, summary, url, currency, bid_count, budget_min, budget_max,
                       client_country, avg_bid, created_at
                FROM bid_history
                WHERE notification_sent = 0 AND title IS NOT NULL
                ORDER BY created_at ASC
            """)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Failed to get unsent notifications: {e}")
            return []

    def get_bid_stats(self, since: str = None) -> dict:
        """Get statistics about bid history (successful bids only).

        Args:
            since: Optional ISO timestamp to filter stats from.

        Returns:
            Dictionary with bid statistics.
        """
        try:
            cursor = self._conn.cursor()
            if since:
                cursor.execute("""
                    SELECT
                        COUNT(*) as bids_placed,
                        AVG(amount) as avg_amount
                    FROM bid_history
                    WHERE success = 1 AND created_at >= ?
                """, (since,))
            else:
                cursor.execute("""
                    SELECT
                        COUNT(*) as bids_placed,
                        AVG(amount) as avg_amount
                    FROM bid_history
                    WHERE success = 1
                """)
            row = cursor.fetchone()
            return {
                "bids_placed": row["bids_placed"] or 0,
                "avg_amount": round(row["avg_amount"] or 0, 2),
            }
        except sqlite3.Error as e:
            logger.error(f"Failed to get bid stats: {e}")
            return {"bids_placed": 0, "avg_amount": 0}

    def get_recent_bids(self, limit: int = 50) -> List[Tuple[int, float, str, str]]:
        """Get the most recent successful bids.

        Args:
            limit: The maximum number of bids to retrieve.

        Returns:
            A list of tuples, each containing (project_id, amount, created_at, description).
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT project_id, amount, created_at, description
                FROM bid_history
                WHERE success = 1
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Failed to get recent bids: {e}")
            return []

    def get_recent_bids_full(self, limit: int = None, since: str = None):
        """Get recent successful bids with ALL stored columns.

        Args:
            limit: Max rows (None = all).
            since: ISO timestamp — only bids after this date.

        Returns sqlite3.Row objects.
        """
        try:
            cursor = self._conn.cursor()
            conditions = ["success = 1"]
            params = []
            if since:
                conditions.append("created_at >= ?")
                params.append(since)
            where = " AND ".join(conditions)
            sql = f"""
                SELECT project_id, amount, period, description, created_at,
                       title, summary, url, currency, bid_count,
                       budget_min, budget_max, client_country, avg_bid, outcome
                FROM bid_history
                WHERE {where}
                ORDER BY created_at DESC
            """
            if limit:
                sql += " LIMIT ?"
                params.append(limit)
            cursor.execute(sql, params)
            return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Failed to get recent bids (full): {e}")
            return []

    def update_bid_outcome(self, project_id: int, outcome: str) -> bool:
        """Cache the win/loss outcome for a bid."""
        try:
            self._conn.execute(
                "UPDATE bid_history SET outcome = ? WHERE project_id = ? AND success = 1",
                (outcome, project_id),
            )
            self._conn.commit()
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update outcome for {project_id}: {e}")
            return False

    def get_outcome_summary(self, since: str = None) -> dict:
        """Get cached outcome counts from bid_history.

        Returns dict: {outcome_value: count, ...} e.g. {'LOSS': 5, 'OPEN': 12, ...}
        """
        try:
            cursor = self._conn.cursor()
            if since:
                cursor.execute("""
                    SELECT outcome, COUNT(*) as cnt
                    FROM bid_history
                    WHERE success = 1 AND created_at >= ?
                    GROUP BY outcome
                """, (since,))
            else:
                cursor.execute("""
                    SELECT outcome, COUNT(*) as cnt
                    FROM bid_history
                    WHERE success = 1
                    GROUP BY outcome
                """)
            return {row["outcome"]: row["cnt"] for row in cursor.fetchall()}
        except sqlite3.Error as e:
            logger.error(f"Failed to get outcome summary: {e}")
            return {}

    def get_bid_outcome(self, project_id: int) -> str | None:
        """Get cached outcome string from bid_outcomes table."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT outcome FROM bid_outcomes WHERE project_id = ?",
                (project_id,),
            )
            row = cursor.fetchone()
            return row["outcome"] if row else None
        except sqlite3.Error:
            return None

    def get_bid_outcome_full(self, project_id: int) -> dict | None:
        """Get cached outcome plus winner comparison data from bid_outcomes.

        Returns:
            Dict with all cached winner fields. None if no row found.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT outcome, winner_amount, winner_proposal_len, winner_proposal, winner_reviews,
                          winner_hourly_rate, winner_reg_date, winner_earnings_score,
                          winner_portfolio_count, my_time_to_bid_sec, winner_time_to_bid_sec
                   FROM bid_outcomes WHERE project_id = ?""",
                (project_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "outcome": row["outcome"],
                "winner_amount": row["winner_amount"],
                "winner_proposal_len": row["winner_proposal_len"],
                "winner_proposal": row["winner_proposal"],
                "winner_reviews": row["winner_reviews"],
                "winner_hourly_rate": row["winner_hourly_rate"],
                "winner_reg_date": row["winner_reg_date"],
                "winner_earnings_score": row["winner_earnings_score"],
                "winner_portfolio_count": row["winner_portfolio_count"],
                "my_time_to_bid_sec": row["my_time_to_bid_sec"],
                "winner_time_to_bid_sec": row["winner_time_to_bid_sec"],
            }
        except sqlite3.Error:
            return None

    def set_bid_outcome(self, project_id: int, outcome: str, winner_detail: dict = None):
        """Cache outcome in bid_outcomes table (upsert). Optionally persist winner comparison data.

        Args:
            project_id: Freelancer project ID.
            outcome: Outcome string ("LOSS", "MY_WIN", "NO_WINNER", "LOSS_SEALED", "ERROR").
            winner_detail: Dict with winner data (for LOSS). Supported keys:
                           winner_amount, winner_proposal, winner_profile,
                           winner_hourly_rate, winner_reg_date, winner_earnings_score,
                           winner_portfolio_count, my_time_to_bid_sec, winner_time_to_bid_sec.
        """
        try:
            winner_amount = None
            winner_proposal_len = None
            winner_proposal_text = None
            winner_reviews = None
            winner_hourly_rate = None
            winner_reg_date = None
            winner_earnings_score = None
            winner_portfolio_count = None
            my_time_to_bid_sec = None
            winner_time_to_bid_sec = None
            if winner_detail:
                winner_amount = winner_detail.get("winner_amount")
                proposal = winner_detail.get("winner_proposal") or ""
                if proposal and not all(c == "x" for c in proposal):  # don't store placeholders
                    winner_proposal_text = proposal
                    winner_proposal_len = len(proposal)
                elif proposal:
                    winner_proposal_len = len(proposal)
                winner_reviews = (winner_detail.get("winner_profile") or {}).get("reviews")
                winner_hourly_rate = winner_detail.get("winner_hourly_rate")
                winner_reg_date = winner_detail.get("winner_reg_date")
                winner_earnings_score = winner_detail.get("winner_earnings_score")
                winner_portfolio_count = winner_detail.get("winner_portfolio_count")
                my_time_to_bid_sec = winner_detail.get("my_time_to_bid_sec")
                winner_time_to_bid_sec = winner_detail.get("winner_time_to_bid_sec")
            self._conn.execute(
                """INSERT INTO bid_outcomes
                       (project_id, outcome, updated_at,
                        winner_amount, winner_proposal_len, winner_proposal, winner_reviews,
                        winner_hourly_rate, winner_reg_date, winner_earnings_score,
                        winner_portfolio_count, my_time_to_bid_sec, winner_time_to_bid_sec)
                   VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id) DO UPDATE SET
                       outcome = excluded.outcome,
                       updated_at = excluded.updated_at,
                       winner_amount = COALESCE(excluded.winner_amount, winner_amount),
                       winner_proposal_len = COALESCE(excluded.winner_proposal_len, winner_proposal_len),
                       winner_proposal = COALESCE(excluded.winner_proposal, winner_proposal),
                       winner_reviews = COALESCE(excluded.winner_reviews, winner_reviews),
                       winner_hourly_rate = COALESCE(excluded.winner_hourly_rate, winner_hourly_rate),
                       winner_reg_date = COALESCE(excluded.winner_reg_date, winner_reg_date),
                       winner_earnings_score = COALESCE(excluded.winner_earnings_score, winner_earnings_score),
                       winner_portfolio_count = COALESCE(excluded.winner_portfolio_count, winner_portfolio_count),
                       my_time_to_bid_sec = COALESCE(excluded.my_time_to_bid_sec, my_time_to_bid_sec),
                       winner_time_to_bid_sec = COALESCE(excluded.winner_time_to_bid_sec, winner_time_to_bid_sec)""",
                (project_id, outcome,
                 winner_amount, winner_proposal_len, winner_proposal_text, winner_reviews,
                 winner_hourly_rate, winner_reg_date, winner_earnings_score,
                 winner_portfolio_count, my_time_to_bid_sec, winner_time_to_bid_sec),
            )
            self._conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Failed to set bid outcome for {project_id}: {e}")

    def get_processed_count(self, since: str = None) -> int:
        """Get count of processed projects.

        Args:
            since: Optional ISO timestamp to filter from.
        """
        try:
            cursor = self._conn.cursor()
            if since:
                cursor.execute(
                    "SELECT COUNT(*) FROM processed_projects WHERE processed_at >= ?",
                    (since,),
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM processed_projects")
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            logger.error(f"Failed to count processed projects: {e}")
            return 0

    # ===== Project Queue Methods (for Custom GPT) =====

    def add_to_queue(
        self,
        project_id: int,
        title: str,
        description: str,
        budget_min: float,
        budget_max: float,
        currency: str,
        client_country: str,
        bid_count: int,
        avg_bid: float,
        url: str,
        time_submitted: datetime = None,
        skill_names: str = None,
        owner_username: str = "",
        owner_display_name: str = "",
        is_preferred_only: bool = False,
    ) -> bool:
        """Add a project to the queue for GPT processing.

        Args:
            project_id: Freelancer project ID
            title: Project title
            description: Project description
            budget_min: Minimum budget
            budget_max: Maximum budget
            currency: Currency code
            client_country: Client's country
            bid_count: Number of bids
            avg_bid: Average bid amount
            url: Project URL
            time_submitted: When project was submitted
            skill_names: Comma-separated skill/tag names for keyword matching
            owner_display_name: Client's display name (public_name from API)

        Returns:
            True if added successfully, False if already exists.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO project_queue
                    (project_id, title, description, budget_min, budget_max,
                     currency, client_country, bid_count, avg_bid, url, time_submitted, status, skill_names, owner_username, owner_display_name, is_preferred_only)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (project_id, title, description, budget_min, budget_max,
                     currency, client_country, bid_count, avg_bid, url, time_submitted, skill_names, owner_username, owner_display_name, 1 if is_preferred_only else 0),
                )
            logger.debug(f"Added project {project_id} to queue")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to add project {project_id} to queue: {e}")
            return False

    def get_next_from_queue(self) -> Optional[dict]:
        """Get the next pending project from queue (freshest first).

        Returns:
            Project dict if available, None if queue is empty.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT * FROM project_queue
                WHERE status = 'pending'
                ORDER BY fetched_at DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get next project from queue: {e}")
            return None

    def mark_queue_status(self, project_id: int, status: str) -> bool:
        """Update the status of a project in the queue.

        Args:
            project_id: The project ID
            status: New status ('pending', 'sent_to_gpt', 'processed')

        Returns:
            True if updated successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE project_queue SET status = ? WHERE project_id = ?",
                    (status, project_id),
                )
            logger.debug(f"Project {project_id} marked as {status}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update queue status for {project_id}: {e}")
            return False

    def get_queue_count(self, status: str = None) -> int:
        """Get count of projects in queue.

        Args:
            status: Filter by status (None for all)
        """
        try:
            cursor = self._conn.cursor()
            if status:
                cursor.execute(
                    "SELECT COUNT(*) FROM project_queue WHERE status = ?",
                    (status,),
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM project_queue")
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            logger.error(f"Failed to count queue: {e}")
            return 0

    def remove_from_queue(self, project_id: int):
        """Remove a project from the queue."""
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM project_queue WHERE project_id = ?",
                    (project_id,),
                )
        except sqlite3.Error as e:
            logger.error(f"Failed to remove {project_id} from queue: {e}")

    def is_in_queue(self, project_id: int) -> bool:
        """Check if a project is already in the queue."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT 1 FROM project_queue WHERE project_id = ?",
                (project_id,),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Failed to check queue for {project_id}: {e}")
            return False

    def get_project_from_queue(self, project_id: int) -> Optional[dict]:
        """Get project data from queue by ID.

        Returns:
            Project data dict or None if not found.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """SELECT project_id, title, description, budget_min, budget_max,
                          currency, client_country, bid_count, avg_bid, url
                   FROM project_queue WHERE project_id = ?""",
                (project_id,),
            )
            row = cursor.fetchone()
            if row:
                return {
                    "project_id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "budget_min": row[3],
                    "budget_max": row[4],
                    "currency": row[5],
                    "client_country": row[6],
                    "bid_count": row[7],
                    "avg_bid": row[8],
                    "url": row[9],
                }
            return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get project {project_id} from queue: {e}")
            return None

    # ===== Pending Bids Methods (shared between API and Telegram) =====

    def add_pending_bid(
        self,
        project_id: int,
        amount: float,
        period: int,
        description: str,
        title: str,
        currency: str = "USD",
        url: str = None,
        bid_count: int = 0,
        summary: str = None,
        budget_min: float = None,
        budget_max: float = None,
        client_country: str = None,
        avg_bid: float = None,
    ) -> bool:
        """Store a pending bid for later confirmation.

        Args:
            project_id: The project ID
            amount: Bid amount
            period: Delivery period in days
            description: Bid proposal text
            title: Project title
            currency: Currency code
            url: Project URL
            bid_count: Current bid count on project
            summary: AI analysis summary
            budget_min: Minimum budget
            budget_max: Maximum budget
            client_country: Client's country
            avg_bid: Average bid on project

        Returns:
            True if stored successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO pending_bids
                    (project_id, amount, period, description, title, currency, url, bid_count,
                     summary, budget_min, budget_max, client_country, avg_bid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, amount, period, description, title, currency, url, bid_count,
                     summary, budget_min, budget_max, client_country, avg_bid),
                )
            logger.debug(f"Stored pending bid for project {project_id}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to store pending bid for {project_id}: {e}")
            return False

    def get_pending_bid(self, project_id: int) -> Optional[dict]:
        """Get pending bid data for a project.

        Args:
            project_id: The project ID

        Returns:
            Bid data dict or None if not found.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT * FROM pending_bids WHERE project_id = ?",
                (project_id,),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get pending bid for {project_id}: {e}")
            return None

    def update_pending_bid(
        self,
        project_id: int,
        amount: float = None,
        description: str = None,
    ) -> Optional[dict]:
        """Update pending bid data.

        Args:
            project_id: The project ID
            amount: New bid amount (if provided)
            description: New proposal text (if provided)

        Returns:
            Updated bid data dict or None if not found.
        """
        try:
            updates = []
            params = []
            if amount is not None:
                updates.append("amount = ?")
                params.append(amount)
            if description is not None:
                updates.append("description = ?")
                params.append(description)

            if not updates:
                return self.get_pending_bid(project_id)

            # Always update the updated_at timestamp when editing
            updates.append("updated_at = CURRENT_TIMESTAMP")

            params.append(project_id)
            with self._conn:
                self._conn.execute(
                    f"UPDATE pending_bids SET {', '.join(updates)} WHERE project_id = ?",
                    params,
                )
            logger.info(f"Updated pending bid for project {project_id}")
            return self.get_pending_bid(project_id)
        except sqlite3.Error as e:
            logger.error(f"Failed to update pending bid for {project_id}: {e}")
            return None

    def get_pending_bid_updated_at(self, project_id: int) -> Optional[str]:
        """Get the updated_at timestamp for a pending bid.

        Args:
            project_id: The project ID

        Returns:
            ISO timestamp string or None if not found.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT updated_at FROM pending_bids WHERE project_id = ?",
                (project_id,),
            )
            row = cursor.fetchone()
            if row:
                return row[0]
            return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get updated_at for {project_id}: {e}")
            return None

    def remove_pending_bid(self, project_id: int) -> bool:
        """Remove pending bid after it's been used.

        Args:
            project_id: The project ID

        Returns:
            True if removed successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM pending_bids WHERE project_id = ?",
                    (project_id,),
                )
            logger.debug(f"Removed pending bid for project {project_id}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to remove pending bid for {project_id}: {e}")
            return False

    def cleanup_old_queue_items(self, max_age_hours: float) -> int:
        """Remove old projects from queue and pending bids.

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of items removed.
        """
        try:
            cursor = self._conn.cursor()

            # Get old pending items AND stuck 'analyzing' items (exception left them frozen)
            cursor.execute("""
                SELECT project_id FROM project_queue
                WHERE status IN ('pending', 'analyzing')
                AND fetched_at < datetime('now', ? || ' hours')
            """, (f"-{max_age_hours}",))
            old_project_ids = [row[0] for row in cursor.fetchall()]

            if not old_project_ids:
                return 0

            with self._conn:
                # Remove from queue
                placeholders = ",".join("?" * len(old_project_ids))
                self._conn.execute(
                    f"DELETE FROM project_queue WHERE project_id IN ({placeholders})",
                    old_project_ids,
                )
                # Remove from pending bids
                self._conn.execute(
                    f"DELETE FROM pending_bids WHERE project_id IN ({placeholders})",
                    old_project_ids,
                )
                # Mark as processed so we don't fetch again
                for pid in old_project_ids:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO processed_projects (project_id) VALUES (?)",
                        (pid,),
                    )

            logger.info(f"Cleaned up {len(old_project_ids)} old projects from queue")
            return len(old_project_ids)
        except sqlite3.Error as e:
            logger.error(f"Failed to cleanup old queue items: {e}")
            return 0

    def reset_for_testing(self) -> dict:
        """Clear processed projects and queue for fresh start (testing mode).

        Returns:
            Dict with counts of cleared items.
        """
        try:
            cursor = self._conn.cursor()

            # Count current items
            cursor.execute("SELECT COUNT(*) FROM processed_projects")
            processed_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM project_queue")
            queue_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM pending_bids")
            pending_count = cursor.fetchone()[0]

            with self._conn:
                self._conn.execute("DELETE FROM processed_projects")
                self._conn.execute("DELETE FROM project_queue")
                self._conn.execute("DELETE FROM pending_bids")

            logger.info(f"RESET: Cleared {processed_count} processed, {queue_count} queue, {pending_count} pending")
            return {
                "processed_cleared": processed_count,
                "queue_cleared": queue_count,
                "pending_cleared": pending_count,
            }
        except sqlite3.Error as e:
            logger.error(f"Failed to reset: {e}")
            return {"error": str(e)}

    def is_paused(self) -> bool:
        """Check if monitoring is paused (shared between processes)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'paused'"
            )
            row = cursor.fetchone()
            return row and row[0] == "true"
        except sqlite3.Error as e:
            logger.error(f"Failed to check paused state: {e}")
            return False

    def set_paused(self, paused: bool) -> bool:
        """Set the paused state (shared between processes)."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('paused', ?, CURRENT_TIMESTAMP)
                    """,
                    ("true" if paused else "false",),
                )
            logger.info(f"Monitoring {'PAUSED' if paused else 'RESUMED'}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set paused state: {e}")
            return False

    def get_poll_interval(self) -> int:
        """Get the poll interval in seconds (shared between processes)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'poll_interval'"
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 300
        except sqlite3.Error as e:
            logger.error(f"Failed to get poll interval: {e}")
            return 300

    def set_poll_interval(self, seconds: int) -> bool:
        """Set the poll interval in seconds (shared between processes)."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('poll_interval', ?, CURRENT_TIMESTAMP)
                    """,
                    (str(seconds),),
                )
            logger.info(f"Poll interval set to {seconds}s")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set poll interval: {e}")
            return False

    def get_max_bid_count(self) -> int:
        """Get the max competitor bid count filter."""
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT value FROM runtime_settings WHERE key = 'max_bid_count'")
            row = cursor.fetchone()
            return int(row[0]) if row else 100
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get max_bid_count: {e}")
            return 100

    def set_max_bid_count(self, count: int) -> bool:
        """Set the max competitor bid count filter."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO runtime_settings (key, value, updated_at) VALUES ('max_bid_count', ?, CURRENT_TIMESTAMP)",
                    (str(count),),
                )
            logger.info(f"max_bid_count set to {count}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set max_bid_count: {e}")
            return False

    def get_bid_adjustment(self) -> int:
        """Get bid price adjustment percentage (e.g. -10 means 10% below market)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT value FROM runtime_settings WHERE key = 'bid_adjustment'")
            row = cursor.fetchone()
            return int(row[0]) if row else -10
        except (sqlite3.Error, ValueError):
            return -10

    def set_bid_adjustment(self, pct: int) -> bool:
        """Set bid price adjustment percentage."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO runtime_settings (key, value, updated_at) VALUES ('bid_adjustment', ?, CURRENT_TIMESTAMP)",
                    (str(pct),),
                )
            logger.info(f"bid_adjustment set to {pct}%")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set bid_adjustment: {e}")
            return False

    def get_budget_range(self) -> tuple[int, int]:
        """Get the budget filter range (min, max) from runtime settings."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'budget_min'"
            )
            row_min = cursor.fetchone()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'budget_max'"
            )
            row_max = cursor.fetchone()
            return (
                int(row_min[0]) if row_min else 50,
                int(row_max[0]) if row_max else 3000,
            )
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get budget range: {e}")
            return (50, 3000)

    def set_budget_range(self, min_budget: int, max_budget: int) -> bool:
        """Set the budget filter range in runtime settings."""
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                       VALUES ('budget_min', ?, CURRENT_TIMESTAMP)""",
                    (str(min_budget),),
                )
                self._conn.execute(
                    """INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                       VALUES ('budget_max', ?, CURRENT_TIMESTAMP)""",
                    (str(max_budget),),
                )
            logger.info(f"Budget range set to ${min_budget}-${max_budget}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set budget range: {e}")
            return False

    def get_min_daily_rate(self) -> int:
        """Get the minimum daily rate in USD from runtime settings."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'min_daily_rate'"
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 100
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get min_daily_rate: {e}")
            return 100

    def set_min_daily_rate(self, rate: int) -> bool:
        """Set the minimum daily rate in USD in runtime settings."""
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                       VALUES ('min_daily_rate', ?, CURRENT_TIMESTAMP)""",
                    (str(rate),),
                )
            logger.info(f"Min daily rate set to ${rate}/day")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set min_daily_rate: {e}")
            return False

    def get_max_project_age(self) -> float:
        """Get max project age in hours from runtime settings."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'max_project_age'"
            )
            row = cursor.fetchone()
            return float(row[0]) if row else 2.0
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get max_project_age: {e}")
            return 2.0

    def set_max_project_age(self, hours: float) -> bool:
        """Set max project age in hours in runtime settings."""
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                       VALUES ('max_project_age', ?, CURRENT_TIMESTAMP)""",
                    (str(hours),),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set max_project_age: {e}")
            return False

    def get_rate_tier2_pct(self) -> int:
        """Get daily rate multiplier (%) for 4-7 day projects (default 65%)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'rate_tier2_pct'"
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 65
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get rate_tier2_pct: {e}")
            return 65

    def set_rate_tier2_pct(self, pct: int) -> bool:
        """Set daily rate multiplier (%) for 4-7 day projects."""
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                       VALUES ('rate_tier2_pct', ?, CURRENT_TIMESTAMP)""",
                    (str(pct),),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set rate_tier2_pct: {e}")
            return False

    def get_rate_tier3_pct(self) -> int:
        """Get daily rate multiplier (%) for 8+ day projects (default 50%)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'rate_tier3_pct'"
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 50
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get rate_tier3_pct: {e}")
            return 50

    def set_rate_tier3_pct(self, pct: int) -> bool:
        """Set daily rate multiplier (%) for 8+ day projects."""
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                       VALUES ('rate_tier3_pct', ?, CURRENT_TIMESTAMP)""",
                    (str(pct),),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set rate_tier3_pct: {e}")
            return False

    def is_verified(self) -> bool:
        """Check if account is verified (can bid on verification-required projects)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'verified_account'"
            )
            row = cursor.fetchone()
            if row:
                return row[0] == "true"
            # Default: not verified (filter verification-required projects)
            return False
        except sqlite3.Error as e:
            logger.error(f"Failed to check verified state: {e}")
            return False

    def set_verified(self, verified: bool) -> bool:
        """Set the verified account status."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('verified_account', ?, CURRENT_TIMESTAMP)
                    """,
                    ("true" if verified else "false",),
                )
            logger.info(f"Verified account set to {verified}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set verified state: {e}")
            return False

    def skip_preferred_only(self) -> bool:
        """Check if preferred-only projects should be skipped."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'skip_preferred_only'"
            )
            row = cursor.fetchone()
            if row:
                return row[0] == "true"
            # Default: skip preferred-only projects
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to check skip_preferred_only state: {e}")
            return True

    def set_skip_preferred_only(self, skip: bool) -> bool:
        """Set the skip preferred-only projects setting."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('skip_preferred_only', ?, CURRENT_TIMESTAMP)
                    """,
                    ("true" if skip else "false",),
                )
            logger.info(f"Skip preferred-only set to {skip}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set skip_preferred_only state: {e}")
            return False

    def is_auto_bid(self) -> bool:
        """Check if auto-bid mode is enabled."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'auto_bid'"
            )
            row = cursor.fetchone()
            if row:
                return row[0] == "true"
            return False
        except sqlite3.Error as e:
            logger.error(f"Failed to check auto_bid state: {e}")
            return False

    def set_auto_bid(self, enabled: bool) -> bool:
        """Set the auto-bid mode."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('auto_bid', ?, CURRENT_TIMESTAMP)
                    """,
                    ("true" if enabled else "false",),
                )
            logger.info(f"Auto-bid set to {enabled}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set auto_bid state: {e}")
            return False

    def get_receive_skipped(self) -> bool:
        """Check if skip notifications are enabled (global setting)."""
        try:
            result = self._conn.execute(
                "SELECT value FROM runtime_settings WHERE key = 'receive_skipped'"
            ).fetchone()
            return result[0] != "false" if result else True  # Default: on
        except sqlite3.Error:
            return True

    def set_receive_skipped(self, enabled: bool) -> bool:
        """Set whether to receive skip notifications."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('receive_skipped', ?, CURRENT_TIMESTAMP)
                    """,
                    ("true" if enabled else "false",),
                )
            logger.info(f"Receive skipped set to {enabled}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set receive_skipped: {e}")
            return False

    def get_notif_mode(self) -> str:
        """Returns 'all', 'bids_plus', or 'bids'. Migrates legacy receive_skipped key."""
        try:
            row = self._conn.execute(
                "SELECT value FROM runtime_settings WHERE key = 'notif_mode'"
            ).fetchone()
            if row:
                return row[0] if row[0] in ("all", "bids_plus", "bids") else "all"
            # Legacy migration: receive_skipped true→all, false→bids
            legacy = self._conn.execute(
                "SELECT value FROM runtime_settings WHERE key = 'receive_skipped'"
            ).fetchone()
            return "bids" if legacy and legacy[0] == "false" else "all"
        except sqlite3.Error:
            return "all"

    def set_notif_mode(self, mode: str) -> bool:
        """Set notification mode: 'all', 'bids_plus', or 'bids'."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO runtime_settings (key, value, updated_at) "
                    "VALUES ('notif_mode', ?, CURRENT_TIMESTAMP)",
                    (mode,),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set notif_mode: {e}")
            return False

    # ===== User Settings Methods (multi-user support) =====

    def get_user(self, chat_id: str) -> Optional[dict]:
        """Get user settings by chat_id.

        Args:
            chat_id: Telegram chat ID

        Returns:
            User settings dict or None if not found.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT * FROM user_settings WHERE chat_id = ?",
                (str(chat_id),),
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except sqlite3.Error as e:
            logger.error(f"Failed to get user {chat_id}: {e}")
            return None

    def add_user(
        self,
        chat_id: str,
        name: str,
        skill_ids: str = "",
        keywords: str = "",
    ) -> bool:
        """Add a new user to the database.

        Args:
            chat_id: Telegram chat ID
            name: User's name
            skill_ids: Comma-separated skill IDs (empty = use global)
            keywords: Comma-separated keywords (empty = all projects)

        Returns:
            True if added successfully, False if already exists.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO user_settings
                    (chat_id, name, skill_ids, keywords, is_active)
                    VALUES (?, ?, ?, ?, 1)
                    """,
                    (str(chat_id), name, skill_ids, keywords),
                )
            logger.info(f"Added user {name} ({chat_id})")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to add user {chat_id}: {e}")
            return False

    def update_user_skills(self, chat_id: str, skill_ids: str) -> bool:
        """Update user's skill_ids.

        Args:
            chat_id: Telegram chat ID
            skill_ids: Comma-separated skill IDs (or empty for default)

        Returns:
            True if updated successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE user_settings SET skill_ids = ?
                    WHERE chat_id = ?
                    """,
                    (skill_ids, str(chat_id)),
                )
            logger.info(f"Updated skills for user {chat_id}: {skill_ids}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update skills for {chat_id}: {e}")
            return False

    def update_user_keywords(self, chat_id: str, keywords: str) -> bool:
        """Update user's keywords.

        Args:
            chat_id: Telegram chat ID
            keywords: Comma-separated keywords (or empty for all projects)

        Returns:
            True if updated successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE user_settings SET keywords = ?
                    WHERE chat_id = ?
                    """,
                    (keywords, str(chat_id)),
                )
            logger.info(f"Updated keywords for user {chat_id}: {keywords}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to update keywords for {chat_id}: {e}")
            return False

    def get_all_active_users(self) -> List[dict]:
        """Get all active users.

        Returns:
            List of user settings dicts.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT * FROM user_settings WHERE is_active = 1"
            )
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Failed to get active users: {e}")
            return []

    def get_all_skill_ids(self) -> List[int]:
        """Get combined skill_ids from all active users.

        Returns:
            List of unique skill IDs. If no users have skills, returns global default.
        """
        try:
            users = self.get_all_active_users()
            all_skills = set()

            for user in users:
                skill_ids_str = user.get("skill_ids", "")
                if skill_ids_str:
                    for skill_id in skill_ids_str.split(","):
                        skill_id = skill_id.strip()
                        if skill_id.isdigit():
                            all_skills.add(int(skill_id))

            if all_skills:
                return list(all_skills)

            # Fall back to global settings if no user-specific skills
            return settings.skill_ids
        except Exception as e:
            logger.error(f"Failed to get combined skill_ids: {e}")
            return settings.skill_ids

    def get_matching_users(
        self,
        title: str,
        description: str,
        skill_names: str = None,
    ) -> List[dict]:
        """Find users whose keywords match the project.

        Args:
            title: Project title
            description: Project description
            skill_names: Comma-separated skill names (tags)

        Returns:
            List of matching user dicts.
        """
        try:
            users = self.get_all_active_users()
            matching = []

            # Combine all searchable text (lowercase)
            search_text = f"{title} {description} {skill_names or ''}".lower()

            for user in users:
                keywords_str = user.get("keywords", "")

                # If no keywords, user gets ALL projects
                if not keywords_str or not keywords_str.strip():
                    matching.append(user)
                    continue

                # Check if any keyword matches
                keywords = [k.strip().lower() for k in keywords_str.split(",") if k.strip()]
                for keyword in keywords:
                    if keyword in search_text:
                        matching.append(user)
                        break

            return matching
        except Exception as e:
            logger.error(f"Failed to get matching users: {e}")
            return []

    def is_project_bidded(self, project_id: int) -> bool:
        """Check if we already placed a bid on this project.

        Args:
            project_id: The project ID

        Returns:
            True if a successful bid exists in history.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT 1 FROM bid_history WHERE project_id = ? AND success = 1",
                (project_id,),
            )
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"Failed to check bid status for {project_id}: {e}")
            return False

    def set_last_poll_stats(
        self,
        found: int,
        filtered: int,
        queued: int,
        already_bid: int,
    ) -> bool:
        """Store stats from last polling cycle.

        Args:
            found: Total projects found from API
            filtered: Projects filtered out
            queued: Projects added to queue
            already_bid: Projects we already bid on

        Returns:
            True if stored successfully.
        """
        try:
            import json
            stats = json.dumps({
                "found": found,
                "filtered": filtered,
                "queued": queued,
                "already_bid": already_bid,
                "timestamp": datetime.now().isoformat(),
            })
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('last_poll_stats', ?, CURRENT_TIMESTAMP)
                    """,
                    (stats,),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to store poll stats: {e}")
            return False

    def set_bot_start_time(self) -> bool:
        """Record when the bot started. Stored as UTC to match SQLite CURRENT_TIMESTAMP."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO runtime_settings (key, value, updated_at)
                    VALUES ('bot_start_time', ?, CURRENT_TIMESTAMP)
                    """,
                    (datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to set bot start time: {e}")
            return False

    def get_total_projects_seen(self) -> int:
        """Get persistent total projects seen (survives RESET_ON_START)."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'total_projects_seen'"
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        except (sqlite3.Error, ValueError) as e:
            logger.error(f"Failed to get total_projects_seen: {e}")
            return 0

    def get_bot_start_time(self) -> Optional[str]:
        """Get when the bot started."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'bot_start_time'"
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except sqlite3.Error as e:
            logger.error(f"Failed to get bot start time: {e}")
            return None

    def get_last_poll_stats(self) -> Optional[dict]:
        """Get stats from last polling cycle.

        Returns:
            Dict with found, filtered, queued, already_bid, timestamp or None.
        """
        try:
            import json
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT value FROM runtime_settings WHERE key = 'last_poll_stats'"
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None
        except (sqlite3.Error, json.JSONDecodeError) as e:
            logger.error(f"Failed to get poll stats: {e}")
            return None

    def toggle_receive_skipped(self, chat_id: str) -> bool:
        """Toggle whether user receives skipped project notifications.

        Args:
            chat_id: Telegram chat ID

        Returns:
            New value of receive_skipped (True = receives, False = doesn't).
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT receive_skipped FROM user_settings WHERE chat_id = ?",
                (str(chat_id),),
            )
            row = cursor.fetchone()
            current = row[0] if row else 1
            logger.debug(f"toggle_receive_skipped: chat_id={chat_id}, current={current}")

            new_value = 0 if current else 1
            with self._conn:
                self._conn.execute(
                    "UPDATE user_settings SET receive_skipped = ? WHERE chat_id = ?",
                    (new_value, str(chat_id)),
                )
            logger.info(f"User {chat_id} receive_skipped toggled: {current} -> {new_value}")

            # Verify it was saved
            cursor.execute(
                "SELECT receive_skipped FROM user_settings WHERE chat_id = ?",
                (str(chat_id),),
            )
            verify = cursor.fetchone()
            logger.debug(f"toggle_receive_skipped: verified value = {verify[0] if verify else 'NOT FOUND'}")

            return bool(new_value)
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle receive_skipped for {chat_id}: {e}")
            return True  # Default to receiving

    def toggle_show_bidstats_details(self, chat_id: str) -> bool:
        """Toggle whether user sees full details in /bidstats.

        Args:
            chat_id: Telegram chat ID

        Returns:
            New value of show_bidstats_details (True = shows details, False = doesn't).
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT show_bidstats_details FROM user_settings WHERE chat_id = ?",
                (str(chat_id),),
            )
            row = cursor.fetchone()
            # Default to True (1) if column is null for some reason
            current = row[0] if row and row[0] is not None else 1

            new_value = 0 if current else 1
            with self._conn:
                self._conn.execute(
                    "UPDATE user_settings SET show_bidstats_details = ? WHERE chat_id = ?",
                    (new_value, str(chat_id)),
                )
            logger.info(f"User {chat_id} show_bidstats_details toggled to {new_value}")
            return bool(new_value)
        except sqlite3.Error as e:
            logger.error(f"Failed to toggle show_bidstats_details for {chat_id}: {e}")
            return True # Default to showing details on error

    def get_users_for_skip_notification(
        self,
        title: str,
        description: str,
        skill_names: str = None,
    ) -> List[dict]:
        """Find users who should receive skip notifications.

        Same as get_matching_users but filters out users with receive_skipped=0.

        Args:
            title: Project title
            description: Project description
            skill_names: Comma-separated skill names (tags)

        Returns:
            List of matching user dicts who want skip notifications.
        """
        matching = self.get_matching_users(title, description, skill_names)
        logger.debug(f"get_users_for_skip_notification: {len(matching)} matching users")
        for u in matching:
            logger.debug(f"  User {u.get('name')}: receive_skipped={u.get('receive_skipped')} (type={type(u.get('receive_skipped'))})")

        # Filter to only users who want skip notifications (receive_skipped != 0)
        result = [u for u in matching if u.get("receive_skipped", 1)]
        logger.debug(f"  After filter: {len(result)} users want skip notifications")
        return result

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("Database connection closed")

    def __del__(self):
        """Cleanup on object destruction."""
        self.close()
