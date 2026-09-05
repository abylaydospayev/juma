from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class MemoryStore:
    """Small durable memory store used directly and through the MCP server."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crew TEXT NOT NULL,
                scope TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in self._connection.execute("PRAGMA table_info(memories)")}
        migrations = {
            "scope_type": "TEXT NOT NULL DEFAULT 'global'",
            "scope_id": "TEXT NOT NULL DEFAULT 'global'",
            "visibility": "TEXT NOT NULL DEFAULT 'shared'",
            "memory_kind": "TEXT NOT NULL DEFAULT 'authored'",
            "status": "TEXT NOT NULL DEFAULT 'accepted'",
            "confidence": "REAL NOT NULL DEFAULT 1.0",
            "workspace_id": "TEXT",
            "thread_id": "TEXT",
            "checksum": "TEXT",
        }
        for name, definition in migrations.items():
            if name not in columns:
                self._connection.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
        self._connection.execute(
            "UPDATE memories SET visibility = scope, scope_type = 'global', scope_id = 'global' "
            "WHERE checksum IS NULL"
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope_type, scope_id, visibility)"
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES ('memory-scoped-v1', 'builtin', ?)",
            (datetime.now(UTC).isoformat(),),
        )
        self._connection.commit()

    def remember(
        self,
        crew: str,
        content: str,
        *,
        scope: str = "shared",
        metadata: dict[str, Any] | None = None,
        workspace_id: str | None = None,
        thread_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        visibility: str | None = None,
        kind: str = "authored",
        status: str = "accepted",
        confidence: float = 1.0,
    ) -> int:
        if not content.strip() or len(content) > 20_000:
            raise ValueError("Memory content must contain 1 to 20,000 characters.")
        visibility = visibility or scope
        scope_type = scope_type or ("thread" if thread_id else "workspace" if workspace_id else "global")
        scope_id = scope_id or thread_id or workspace_id or "global"
        checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO memories "
                "(crew, scope, content, metadata, created_at, scope_type, scope_id, visibility, "
                "memory_kind, status, confidence, workspace_id, thread_id, checksum) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    crew,
                    visibility,
                    content,
                    json.dumps(metadata or {}),
                    datetime.now(UTC).isoformat(),
                    scope_type,
                    scope_id,
                    visibility,
                    kind,
                    status,
                    max(0.0, min(1.0, confidence)),
                    workspace_id,
                    thread_id,
                    checksum,
                ),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def search(
        self,
        query: str,
        *,
        crew: str | None = None,
        scope: str | None = None,
        limit: int = 10,
        workspace_id: str | None = None,
        thread_id: str | None = None,
        include_pending: bool = False,
    ) -> list[dict]:
        # SQLite LIKE is useful as a fallback, but ranking token overlap makes recall
        # more useful once the memory table grows beyond a handful of rows.
        sql = "SELECT * FROM memories WHERE 1 = 1"
        params: list[Any] = []
        if crew:
            sql += " AND (scope = 'shared' OR crew = ?)"
            params.append(crew)
        if scope:
            sql += " AND scope = ?"
            params.append(scope)
        if workspace_id:
            sql += " AND (workspace_id IS NULL OR workspace_id = ? OR scope_type = 'global')"
            params.append(workspace_id)
        if thread_id:
            sql += " AND (thread_id IS NULL OR thread_id = ? OR scope_type IN ('global', 'workspace'))"
            params.append(thread_id)
        if not include_pending:
            sql += " AND status = 'accepted'"
        sql += " ORDER BY id DESC LIMIT 500"
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        query_tokens = set(re.findall(r"[\w-]{2,}", query.casefold()))
        ranked: list[tuple[float, dict]] = []
        for row in rows:
            item = self._row(row)
            content = item["content"].casefold()
            content_tokens = set(re.findall(r"[\w-]{2,}", content))
            overlap = len(query_tokens & content_tokens)
            exact = 1.0 if query.casefold() in content else 0.0
            if query_tokens and overlap == 0 and not exact:
                continue
            ranked.append((overlap * 2 + exact + item["id"] / 1_000_000, item))
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        return [item for _, item in ranked[: max(1, min(limit, 100))]]

    def recent(
        self,
        *,
        crew: str | None = None,
        scope: str | None = None,
        limit: int = 10,
        workspace_id: str | None = None,
        thread_id: str | None = None,
        include_pending: bool = False,
    ) -> list[dict]:
        sql = "SELECT * FROM memories"
        params: list[Any] = []
        filters: list[str] = []
        if crew:
            filters.append("(scope = 'shared' OR crew = ?)")
            params.append(crew)
        if scope:
            filters.append("scope = ?")
            params.append(scope)
        if workspace_id:
            filters.append("(workspace_id IS NULL OR workspace_id = ? OR scope_type = 'global')")
            params.append(workspace_id)
        if thread_id:
            filters.append("(thread_id IS NULL OR thread_id = ? OR scope_type IN ('global', 'workspace'))")
            params.append(thread_id)
        if not include_pending:
            filters.append("status = 'accepted'")
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._row(row) for row in rows]

    def accept_inference(self, memory_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE memories SET status = 'accepted' WHERE id = ? AND memory_kind = 'inference'",
                (memory_id,),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    def reject_inference(self, memory_id: int) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE memories SET status = 'rejected' WHERE id = ? AND memory_kind = 'inference'",
                (memory_id,),
            )
            self._connection.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def close(self) -> None:
        self._connection.close()
