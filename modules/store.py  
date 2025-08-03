# bid-assist/modules/store.py

import sqlite3
import logging

DB_FILE = "processed_projects.db"

class ProjectStore:
    """
    Класс для управления базой данных обработанных проектов (SQLite).
    Гарантирует, что один и тот же проект не будет предложен дважды.
    """
    def __init__(self, db_path=DB_FILE):
        """Инициализирует подключение к БД и создает таблицу, если ее нет."""
        self.db_path = db_path
        try:
            self._conn = sqlite3.connect(self.db_path)
            self._create_table()
        except sqlite3.Error as e:
            logging.error(f"Ошибка при подключении к базе данных {self.db_path}: {e}")
            raise

    def _create_table(self):
        """Создает таблицу 'processed_projects', если она не существует."""
        try:
            with self._conn:
                self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS processed_projects (
                        project_id INTEGER PRIMARY KEY
                    )
                """)
        except sqlite3.Error as e:
            logging.error(f"Ошибка при создании таблицы: {e}")

    def add_project(self, project_id: int):
        """Добавляет ID проекта в базу данных. Игнорирует, если ID уже существует."""
        try:
            with self._conn:
                self._conn.execute(
                    "INSERT OR IGNORE INTO processed_projects (project_id) VALUES (?)",
                    (project_id,)
                )
        except sqlite3.Error as e:
            logging.error(f"Ошибка при добавлении project_id={project_id} в БД: {e}")

    def is_processed(self, project_id: int) -> bool:
        """Проверяет, был ли проект обработан ранее."""
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1 FROM processed_projects WHERE project_id = ?", (project_id,))
            return cursor.fetchone() is not None
        except sqlite3.Error as e:
            logging.error(f"Ошибка при проверке project_id={project_id}: {e}")
            return False # В случае ошибки считаем, что не обработан, чтобы не пропустить

    def __del__(self):
        """Закрывает соединение с БД при уничтожении объекта."""
        if hasattr(self, '_conn') and self._conn:
            self._conn.close()