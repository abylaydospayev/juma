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
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=30000")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (migration_id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        table_info = self._connection.execute("PRAGMA table_info(preferences)").fetchall()
        columns = {row[1] for row in table_info}
        # The original schema made ``key`` globally unique.  Rebuild it once so
        # scope precedence can safely store the same key at multiple levels.
        if "scope_type" not in columns:
            self._connection.execute("ALTER TABLE preferences RENAME TO preferences_legacy")
            self._connection.execute(
                """CREATE TABLE preferences (
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    scope_type TEXT NOT NULL DEFAULT 'global',
                    scope_id TEXT NOT NULL DEFAULT 'global',
                    deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(scope_type, scope_id, key)
                )"""
            )
            self._connection.execute(
                "INSERT INTO preferences(key, value, updated_at) SELECT key, value, updated_at FROM preferences_legacy"
            )
            self._connection.execute("DROP TABLE preferences_legacy")
            columns = {"key", "value", "updated_at", "scope_type", "scope_id", "deleted"}
        if "scope_type" not in columns:
            self._connection.execute("ALTER TABLE preferences ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'global'")
        if "scope_id" not in columns:
            self._connection.execute("ALTER TABLE preferences ADD COLUMN scope_id TEXT NOT NULL DEFAULT 'global'")
        if "deleted" not in columns:
            self._connection.execute("ALTER TABLE preferences ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
        self._connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_preferences_scope_key ON preferences(scope_type, scope_id, key)"
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES ('preferences-scoped-v1', 'builtin', ?)",
            (datetime.now(UTC).isoformat(),),
        )
        self._connection.commit()

    def all(
        self,
        *,
        scope_type: str = "global",
        scope_id: str = "global",
        include_deleted: bool = False,
    ) -> dict[str, str] | dict[str, tuple[str, bool]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT key, value, deleted FROM preferences WHERE scope_type = ? AND scope_id = ? "
                + ("ORDER BY key" if include_deleted else "AND deleted = 0 ORDER BY key"),
                (scope_type, scope_id),
            ).fetchall()
        if include_deleted:
            return {str(key): (str(value), bool(deleted)) for key, value, deleted in rows}
        return {str(key): str(value) for key, value, _ in rows}

    def get(
        self,
        key: str,
        default: str | None = None,
        *,
        scope_type: str = "global",
        scope_id: str = "global",
    ) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM preferences WHERE key = ? AND scope_type = ? AND scope_id = ? AND deleted = 0",
                (key, scope_type, scope_id),
            ).fetchone()
        return str(row[0]) if row is not None else default

    def set(
        self,
        key: str,
        value: str,
        *,
        scope_type: str = "global",
        scope_id: str = "global",
    ) -> dict[str, str]:
        key = key.strip()
        if not key or len(key) > 80:
            raise ValueError("Preference keys must contain 1 to 80 characters.")
        if len(value) > 4000:
            raise ValueError("Preference values must contain at most 4000 characters.")
        with self._lock:
            existing = self._connection.execute(
                "SELECT rowid FROM preferences WHERE key = ? AND scope_type = ? AND scope_id = ?",
                (key, scope_type, scope_id),
            ).fetchone()
            if existing:
                self._connection.execute(
                    "UPDATE preferences SET value = ?, updated_at = ?, deleted = 0 WHERE rowid = ?",
                    (value, datetime.now(UTC).isoformat(), existing[0]),
                )
            else:
                self._connection.execute(
                    "INSERT INTO preferences (key, value, updated_at, scope_type, scope_id, deleted) VALUES (?, ?, ?, ?, ?, 0)",
                    (key, value, datetime.now(UTC).isoformat(), scope_type, scope_id),
                )
            self._connection.commit()
        # Keep the original command/API response shape; scope metadata is
        # persisted but does not break existing clients.
        return {"key": key, "value": value}

    def delete(self, key: str, *, scope_type: str = "global", scope_id: str = "global") -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE preferences SET deleted = 1, updated_at = ? WHERE key = ? AND scope_type = ? AND scope_id = ?",
                (datetime.now(UTC).isoformat(), key, scope_type, scope_id),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._connection.close()
