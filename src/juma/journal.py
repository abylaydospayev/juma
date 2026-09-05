"""Private executor journal for crash recovery and compensation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class ExecutorJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS action_journal (
                journal_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                status TEXT NOT NULL,
                observed_before TEXT NOT NULL,
                observed_after TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.commit()
        self.lock = Lock()

    def begin(self, run_id: str, action_id: str, observed_before: dict[str, Any]) -> str:
        journal_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self.lock:
            self.connection.execute(
                "INSERT INTO action_journal VALUES (?, ?, ?, 'started', ?, NULL, ?, ?)",
                (journal_id, run_id, action_id, json.dumps(observed_before), now, now),
            )
            self.connection.commit()
        return journal_id

    def finish(self, journal_id: str, *, status: str, observed_after: dict[str, Any] | None = None) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE action_journal SET status = ?, observed_after = ?, updated_at = ? WHERE journal_id = ?",
                (status, json.dumps(observed_after) if observed_after is not None else None, datetime.now(UTC).isoformat(), journal_id),
            )
            self.connection.commit()

    def incomplete(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM action_journal WHERE status IN ('started', 'running') ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()
