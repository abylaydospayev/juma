from __future__ import annotations

from typing import Any, TypedDict

from mcp.server import MCPServer

from .config import Settings
from .memory import MemoryStore

mcp = MCPServer(
    "juma-memory",
    version="0.1.0",
    instructions="Shared long-term memory for juma crews.",
)


class RememberResult(TypedDict):
    id: int
    stored: bool


class MemoryRecord(TypedDict):
    id: int
    crew: str
    scope: str
    content: str
    metadata: dict[str, Any]
    created_at: str


def _store() -> MemoryStore:
    settings = Settings.from_env()
    settings.ensure_directories()
    return MemoryStore(settings.memory_db)


@mcp.tool()
def remember(crew: str, content: str, scope: str = "shared") -> RememberResult:
    """Store a memory for a juma crew."""
    store = _store()
    try:
        memory_id = store.remember(crew, content, scope=scope)
        return {"id": memory_id, "stored": True}
    finally:
        store.close()


@mcp.tool()
def search_memory(query: str, crew: str | None = None, limit: int = 10) -> list[MemoryRecord]:
    """Search memories visible to a crew."""
    store = _store()
    try:
        return store.search(query, crew=crew, limit=limit)
    finally:
        store.close()


@mcp.resource("memory://recent/{crew}")
def recent_memories(crew: str) -> str:
    """Read recent memories visible to a crew."""
    store = _store()
    try:
        import json

        return json.dumps(store.recent(crew=crew), indent=2)
    finally:
        store.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
