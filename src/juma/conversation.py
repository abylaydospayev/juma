from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class ConversationStore:
    """Durable, lightweight chat history separate from LangGraph checkpoints."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = Lock()
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                agent TEXT,
                status TEXT,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id)"
        )
        self._connection.commit()

    def append(
        self,
        thread_id: str,
        role: str,
        content: str,
        *,
        agent: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO messages "
                "(thread_id, role, content, agent, status, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    role,
                    content,
                    agent,
                    status,
                    json.dumps(metadata or {}),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def history(self, thread_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 100))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE thread_id = ? "
                "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (thread_id, safe_limit),
            ).fetchall()
        return [self._row(row) for row in rows]

    def list_threads(self, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 200))
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT thread_id, MAX(id) AS last_id,
                    MAX(created_at) AS updated_at,
                    COALESCE(
                        (SELECT content FROM messages first_message
                         WHERE first_message.thread_id = messages.thread_id
                           AND first_message.role = 'user'
                         ORDER BY first_message.id ASC LIMIT 1),
                        'New conversation'
                    ) AS title
                FROM messages
                GROUP BY thread_id
                ORDER BY last_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_last_assistant(
        self,
        thread_id: str,
        content: str,
        *,
        agent: str | None,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id FROM messages WHERE thread_id = ? AND role = 'assistant' "
                "ORDER BY id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO messages "
                    "(thread_id, role, content, agent, status, metadata, created_at) "
                    "VALUES (?, 'assistant', ?, ?, ?, ?, ?)",
                    (
                        thread_id,
                        content,
                        agent,
                        status,
                        json.dumps(metadata or {}),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                self._connection.commit()
                return
            self._connection.execute(
                "UPDATE messages SET content = ?, agent = ?, status = ?, metadata = ? WHERE id = ?",
                (content, agent, status, json.dumps(metadata or {}), row["id"]),
            )
            self._connection.commit()

    def replace_content(self, message_id: int, content: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE messages SET content = ? WHERE id = ?", (content, message_id)
            )
            self._connection.commit()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def close(self) -> None:
        self._connection.close()
