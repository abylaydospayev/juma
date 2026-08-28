from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .audit import AuditLog
from .config import Settings
from .conversation import ConversationStore
from .graph import build_graph
from .memory import MemoryStore
from .models import ModelClient, OpenAIResponsesModel


class Juma:
    def __init__(self, settings: Settings | None = None, *, model: ModelClient | None = None):
        self.settings = settings or Settings.from_env()
        self.settings.ensure_directories()
        self._checkpoint_connection = sqlite3.connect(
            self.settings.checkpoint_db, check_same_thread=False
        )
        self.checkpointer = SqliteSaver(self._checkpoint_connection)
        self.model = model or OpenAIResponsesModel(self.settings)
        self.memory = MemoryStore(self.settings.memory_db)
        self.conversations = ConversationStore(self.settings.data_dir / "conversations.sqlite")
        self.audit = AuditLog(self.settings.audit_log)
        self.graph = build_graph(self.checkpointer, self.model, self.memory)

    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def ask(self, request: str, *, thread_id: str | None = None) -> dict[str, Any]:
        thread_id = thread_id or str(uuid.uuid4())
        history = self.conversations.history(thread_id, limit=20)
        self.audit.record(
            "run_started",
            thread_id=thread_id,
            request_length=len(request),
            history_messages=len(history),
        )
        try:
            result = self.graph.invoke(
                {
                    "request": request,
                    "status": "routing",
                    "conversation_history": [
                        {
                            "role": item["role"],
                            "content": item["content"],
                            "agent": item.get("agent"),
                            "status": item.get("status"),
                        }
                        for item in history
                    ],
                    "events": [],
                },
                config=self._config(thread_id),
            )
        except Exception as exc:
            self.audit.record("run_failed", thread_id=thread_id, error=type(exc).__name__)
            raise
        envelope = self._envelope(thread_id, result)
        self.conversations.append(thread_id, "user", request)
        self.conversations.append(
            thread_id,
            "assistant",
            result.get("response", ""),
            agent=result.get("target_agent"),
            status=envelope["status"],
            metadata={
                "events": result.get("events", []),
                "interrupt": envelope.get("interrupts", [None])[0],
                "route_confidence": result.get("route_confidence"),
                "route_reason": result.get("route_reason"),
            },
        )
        self.audit.record(
            "run_finished",
            thread_id=thread_id,
            status=envelope["status"],
            agent=result.get("target_agent"),
            usage=getattr(self.model, "last_usage", {}),
        )
        return envelope

    def resume(self, thread_id: str, *, approved: bool, feedback: str = "") -> dict[str, Any]:
        self.audit.record(
            "approval_decision",
            thread_id=thread_id,
            approved=approved,
            feedback_length=len(feedback),
        )
        try:
            result = self.graph.invoke(
                Command(resume={"approved": approved, "feedback": feedback}),
                config=self._config(thread_id),
            )
        except Exception as exc:
            self.audit.record("run_failed", thread_id=thread_id, error=type(exc).__name__)
            raise
        envelope = self._envelope(thread_id, result)
        self.conversations.update_last_assistant(
            thread_id,
            result.get("response", ""),
            agent=result.get("target_agent"),
            status=envelope["status"],
            metadata={
                "events": result.get("events", []),
                "interrupt": None,
                "route_confidence": result.get("route_confidence"),
                "route_reason": result.get("route_reason"),
            },
        )
        self.audit.record(
            "run_finished",
            thread_id=thread_id,
            status=envelope["status"],
            usage=getattr(self.model, "last_usage", {}),
        )
        return envelope

    def history(self, thread_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.conversations.history(thread_id, limit=limit)

    def threads(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.conversations.list_threads(limit=limit)

    def remember(self, crew: str, content: str, *, scope: str = "shared") -> int:
        return self.memory.remember(crew, content, scope=scope)

    @staticmethod
    def _envelope(thread_id: str, result: dict) -> dict[str, Any]:
        interrupts = result.get("__interrupt__", ())
        if interrupts:
            values = [item.value for item in interrupts]
            return {
                "thread_id": thread_id,
                "status": "waiting_approval",
                "interrupts": values,
                "state": {key: value for key, value in result.items() if key != "__interrupt__"},
            }
        return {"thread_id": thread_id, "status": result["status"], "state": result}

    def close(self) -> None:
        self.conversations.close()
        self.memory.close()
        self._checkpoint_connection.close()

    def __enter__(self) -> Juma:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
