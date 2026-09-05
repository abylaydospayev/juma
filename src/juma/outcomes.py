"""Indexed, privacy-preserving routing telemetry."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class OutcomeStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS outcomes (
                outcome_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                changeset_id TEXT,
                crew TEXT,
                tool TEXT,
                strategy TEXT,
                result_code TEXT NOT NULL,
                duration_ms INTEGER,
                input_tokens INTEGER,
                output_tokens INTEGER,
                created_at TEXT NOT NULL,
                metadata TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_outcomes_route ON outcomes(workspace_id, crew, strategy, result_code)"
        )
        self.connection.commit()
        self.lock = Lock()

    def record(self, *, run_id: str, thread_id: str, workspace_id: str, result_code: str, **fields: Any) -> str:
        outcome_id = str(uuid.uuid4())
        usage = fields.pop("usage", {}) or {}
        known = {"changeset_id", "crew", "tool", "strategy", "duration_ms"}
        values = {key: fields.pop(key, None) for key in known}
        with self.lock:
            self.connection.execute(
                "INSERT INTO outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outcome_id,
                    run_id,
                    thread_id,
                    workspace_id,
                    values["changeset_id"],
                    values["crew"],
                    values["tool"],
                    values["strategy"],
                    result_code,
                    values["duration_ms"],
                    usage.get("input_tokens"),
                    usage.get("output_tokens"),
                    datetime.now(UTC).isoformat(),
                    json.dumps(fields, ensure_ascii=False),
                ),
            )
            self.connection.commit()
        return outcome_id

    def recent(self, workspace_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM outcomes WHERE workspace_id = ? ORDER BY created_at DESC LIMIT ?",
                (workspace_id, max(1, min(limit, 500))),
            ).fetchall()
        names = [item[0] for item in self.connection.execute("PRAGMA table_info(outcomes)").fetchall()]
        result = []
        for row in rows:
            item = dict(zip(names, row, strict=False))
            item["metadata"] = json.loads(item["metadata"])
            result.append(item)
        return result

    def close(self) -> None:
        self.connection.close()
