from __future__ import annotations

import json
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
        self._connection.commit()

    def remember(
        self,
        crew: str,
        content: str,
        *,
        scope: str = "shared",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO memories "
                "(crew, scope, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    crew,
                    scope,
                    content,
                    json.dumps(metadata or {}),
                    datetime.now(UTC).isoformat(),
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
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        with self._lock:
            rows = self._connection.execute(sql, params).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def close(self) -> None:
        self._connection.close()
