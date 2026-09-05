"""SQLite persistence for immutable changesets and decisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from .changeset import Changeset


class ChangesetStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS changesets (
                changeset_id TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL UNIQUE,
                run_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                status TEXT NOT NULL,
                document TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.connection.execute("CREATE INDEX IF NOT EXISTS idx_changesets_run ON changesets(run_id)")
        self.connection.commit()
        self.lock = Lock()

    def create(self, changeset: Changeset) -> Changeset:
        candidate = changeset.with_fingerprint()
        now = datetime.now(UTC).isoformat()
        with self.lock:
            self.connection.execute(
                "INSERT INTO changesets VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(candidate.changeset_id), candidate.fingerprint, str(candidate.run_id),
                    candidate.workspace_id, candidate.status,
                    json.dumps(candidate.model_dump(mode="json"), separators=(",", ":")), now, now,
                ),
            )
            self.connection.commit()
        return candidate

    def get(self, changeset_id: str) -> Changeset | None:
        with self.lock:
            row = self.connection.execute("SELECT document FROM changesets WHERE changeset_id = ?", (changeset_id,)).fetchone()
        return Changeset.model_validate(json.loads(row[0])) if row else None

    def decide(self, changeset_id: str, *, approved: bool, fingerprint: str | None) -> Changeset:
        with self.lock:
            row = self.connection.execute("SELECT document FROM changesets WHERE changeset_id = ?", (changeset_id,)).fetchone()
            if row is None:
                raise KeyError(changeset_id)
            current = Changeset.model_validate(json.loads(row[0]))
            if approved and fingerprint != current.fingerprint:
                raise ValueError("Decision fingerprint does not match the immutable changeset.")
            new_status = "queued" if approved else "rejected"
            updated = current.model_copy(update={"status": new_status, "updated_at": datetime.now(UTC)})
            self.connection.execute(
                "UPDATE changesets SET status = ?, document = ?, updated_at = ? WHERE changeset_id = ?",
                (new_status, json.dumps(updated.model_dump(mode="json"), separators=(",", ":")), updated.updated_at.isoformat(), changeset_id),
            )
            self.connection.commit()
            return updated

    def close(self) -> None:
        self.connection.close()
