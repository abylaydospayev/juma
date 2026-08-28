"""Durable user preferences shared by Juma's crews."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock


class PreferenceStore:
    """Store small user preferences in a separate SQLite database."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self._lock = Lock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._connection.commit()

    def all(self) -> dict[str, str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT key, value FROM preferences ORDER BY key"
            ).fetchall()
        return {str(key): str(value) for key, value in rows}

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM preferences WHERE key = ?", (key,)
            ).fetchone()
        return str(row[0]) if row is not None else default

    def set(self, key: str, value: str) -> dict[str, str]:
        key = key.strip()
        if not key or len(key) > 80:
            raise ValueError("Preference keys must contain 1 to 80 characters.")
        if len(value) > 4000:
            raise ValueError("Preference values must contain at most 4000 characters.")
        with self._lock:
            self._connection.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, datetime.now(UTC).isoformat()),
            )
            self._connection.commit()
        return {"key": key, "value": value}

    def delete(self, key: str) -> bool:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM preferences WHERE key = ?", (key,))
            self._connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._connection.close()
