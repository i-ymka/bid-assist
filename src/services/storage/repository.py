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

                # Bid history table (NEW)
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
    ) -> bool:
        """Record a bid attempt in history.

        Args:
            project_id: The project ID.
            amount: Bid amount.
            period: Delivery period in days.
            description: Bid description text.
            success: Whether the bid was placed successfully.
            error_message: Error message if bid failed.

        Returns:
            True if recorded successfully.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO bid_history
                    (project_id, amount, period, description, success, error_message)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, amount, period, description, int(success), error_message),
                )
            logger.info(f"Bid record added for project {project_id}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Failed to record bid for project {project_id}: {e}")
            return False

    def get_bid_stats(self) -> dict:
        """Get statistics about bid history.

        Returns:
            Dictionary with bid statistics.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute("""
                SELECT
                    COUNT(*) as total_bids,
                    SUM(success) as successful_bids,
                    AVG(amount) as avg_amount
                FROM bid_history
            """)
            row = cursor.fetchone()
            return {
                "total_bids": row["total_bids"] or 0,
                "successful_bids": row["successful_bids"] or 0,
                "avg_amount": round(row["avg_amount"] or 0, 2),
            }
        except sqlite3.Error as e:
            logger.error(f"Failed to get bid stats: {e}")
            return {"total_bids": 0, "successful_bids": 0, "avg_amount": 0}

    def get_processed_count(self) -> int:
        """Get count of processed projects."""
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM processed_projects")
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            logger.error(f"Failed to count processed projects: {e}")
            return 0

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.debug("Database connection closed")

    def __del__(self):
        """Cleanup on object destruction."""
        self.close()
