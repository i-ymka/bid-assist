"""Shared SQLite cache for Call 1 (feasibility analysis) results across accounts.

Both yehia and ymka processes access this DB concurrently. WAL mode ensures
safe multi-writer access. The cache prevents duplicate Gemini Call 1 invocations
when both accounts discover the same project.
"""

import sqlite3
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SharedAnalysisRepository:
    """Cross-account cache for AI feasibility analysis (Call 1) results."""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._connect()
        self._init_db()

    def _connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        # WAL mode: allows concurrent readers + one writer without blocking
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def _init_db(self):
        with self._conn:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS shared_analysis (
                    project_id INTEGER PRIMARY KEY,
                    verdict     TEXT,
                    days        INTEGER,
                    summary     TEXT,
                    status      TEXT NOT NULL DEFAULT 'in_progress',
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS project_colors (
                    project_id  INTEGER PRIMARY KEY,
                    color_index INTEGER NOT NULL
                )
            """)

    # ------------------------------------------------------------------
    # Public interface — all methods are thread-safe via _lock
    # ------------------------------------------------------------------

    def try_claim(self, project_id: int) -> bool:
        """Atomically claim the right to run Call 1 for this project."""
        try:
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO shared_analysis (project_id, status) VALUES (?, 'in_progress')",
                    (project_id,),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as e:
            logger.error(f"shared_analysis try_claim({project_id}): {e}")
            return False

    def get_result(self, project_id: int) -> Optional[dict]:
        """Return cached Call 1 result if available and fresh (< 24h)."""
        try:
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT verdict, days, summary, status
                    FROM shared_analysis
                    WHERE project_id = ?
                      AND status IN ('done', 'skip')
                      AND created_at > datetime('now', '-24 hours')
                    """,
                    (project_id,),
                ).fetchone()
            if row:
                return {"verdict": row["verdict"], "days": row["days"], "summary": row["summary"]}
            return None
        except sqlite3.Error as e:
            logger.error(f"shared_analysis get_result({project_id}): {e}")
            return None

    def store_result(self, project_id: int, verdict: str, days: int, summary: str):
        """Store Call 1 result and mark slot as done/skip."""
        status = "skip" if verdict == "SKIP" else "done"
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    UPDATE shared_analysis
                    SET verdict = ?, days = ?, summary = ?, status = ?,
                        created_at = CURRENT_TIMESTAMP
                    WHERE project_id = ?
                    """,
                    (verdict, days, summary, status, project_id),
                )
        except sqlite3.Error as e:
            logger.error(f"shared_analysis store_result({project_id}): {e}")

    def is_claimed(self, project_id: int) -> bool:
        """Return True if project is already in shared_analysis (in_progress or finished)."""
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT 1 FROM shared_analysis WHERE project_id = ?",
                    (project_id,),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def release_claim(self, project_id: int):
        """Delete an in_progress slot so other accounts can retry later."""
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    "DELETE FROM shared_analysis WHERE project_id = ? AND status = 'in_progress'",
                    (project_id,),
                )
        except sqlite3.Error as e:
            logger.error(f"shared_analysis release_claim({project_id}): {e}")

    def release_stale_claims(self, max_age_minutes: int = 30) -> int:
        """Release in_progress claims older than max_age_minutes."""
        try:
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    "DELETE FROM shared_analysis WHERE status = 'in_progress' AND created_at < datetime('now', ? || ' minutes')",
                    (f"-{max_age_minutes}",),
                )
                removed = cursor.rowcount
                if removed:
                    logger.info(f"shared_analysis: released {removed} stale in_progress claims (>{max_age_minutes}min)")
                return removed
        except sqlite3.Error as e:
            logger.error(f"shared_analysis release_stale_claims: {e}")
            return 0

    def get_or_assign_color(self, project_id: int, palette_size: int) -> int:
        """Atomically assign the next round-robin color index to a project, or return existing."""
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
        except sqlite3.Error as e:
            logger.error(f"shared_analysis get_or_assign_color({project_id}): {e}")
            return 0

    def cleanup_stale(self, max_age_hours: float = 24) -> int:
        """Remove stale in_progress entries and expired cache rows."""
        try:
            with self._lock, self._conn:
                cursor = self._conn.execute(
                    "DELETE FROM shared_analysis WHERE created_at < datetime('now', ? || ' hours')",
                    (f"-{max_age_hours}",),
                )
                removed = cursor.rowcount
                if removed:
                    logger.info(f"shared_analysis: removed {removed} stale entries")
                return removed
        except sqlite3.Error as e:
            logger.error(f"shared_analysis cleanup_stale: {e}")
            return 0
