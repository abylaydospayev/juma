from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class AuditLog:
    """Append-only local operational log without storing model prompts by default."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = Lock()

    def record(self, event: str, *, thread_id: str, **fields: Any) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "thread_id": thread_id,
            **fields,
        }
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
