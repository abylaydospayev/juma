from __future__ import annotations

import hmac
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
from .patches import PatchManager


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
        self.patch_manager = PatchManager(self.settings.resolved_workspace_root)
        self.graph = build_graph(
            self.checkpointer,
            self.model,
            self.memory,
            self.patch_manager,
        )

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
                    "proposed_action": None,
                    "patch_result": None,
                    "rollback_available": False,
                    "approval": None,
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
                **self._metadata(result),
                "interrupt": envelope.get("interrupts", [None])[0],
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

    def resume(
        self,
        thread_id: str,
        *,
        approved: bool,
        feedback: str = "",
        action_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(thread_id))
        interrupts = [
            item
            for task in snapshot.tasks
            for item in getattr(task, "interrupts", ())
        ]
        if not interrupts:
            raise ValueError(f"Thread {thread_id!r} is not waiting for approval.")
        pending_action = dict(snapshot.values).get("proposed_action") or {}
        if pending_action.get("kind") == "code.patch":
            expected_fingerprint = pending_action.get("fingerprint")
            if not (
                isinstance(action_fingerprint, str)
                and isinstance(expected_fingerprint, str)
                and hmac.compare_digest(action_fingerprint, expected_fingerprint)
            ):
                raise ValueError(
                    "This patch approval requires the exact action fingerprint "
                    "shown in the preview."
                )
        self.audit.record(
            "approval_decision",
            thread_id=thread_id,
            approved=approved,
            feedback_length=len(feedback),
            action_fingerprint=action_fingerprint,
        )
        try:
            result = self.graph.invoke(
                Command(
                    resume={
                        "approved": approved,
                        "feedback": feedback,
                        "action_fingerprint": action_fingerprint,
                    }
                ),
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
            metadata=self._metadata(result),
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

    def rollback(
        self,
        thread_id: str,
        *,
        action_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.graph.get_state(self._config(thread_id))
        state = dict(snapshot.values)
        action = state.get("proposed_action") or {}
        patch = action.get("patch")
        if not state.get("rollback_available") or not patch:
            raise ValueError("No failed patch is available to roll back for this thread.")
        expected_fingerprint = action.get("fingerprint")
        if not (
            isinstance(action_fingerprint, str)
            and isinstance(expected_fingerprint, str)
            and hmac.compare_digest(action_fingerprint, expected_fingerprint)
        ):
            raise ValueError(
                "This patch rollback requires the exact action fingerprint "
                "shown in the preview."
            )
        patch_result = state.get("patch_result") or {}
        result = self.patch_manager.rollback(
            patch,
            expected_post_apply_hashes=patch_result.get("post_apply_hashes"),
            expected_pre_apply_hashes=patch_result.get("pre_apply_hashes"),
        )
        if result["status"] == "rolled_back":
            response = state["response"] + " The patch was rolled back and the tests were rerun."
            status = "completed"
        else:
            response = state["response"] + f" Rollback failed: {result['error']}"
            status = "failed"
        event = {"source": "patch", "message": f"Rollback result: {result['status']}."}
        updated = {
            **state,
            "response": response,
            "patch_result": result,
            "rollback_available": False,
            "status": status,
            "events": [*state.get("events", []), event],
        }
        self.graph.update_state(
            self._config(thread_id),
            {
                "response": response,
                "patch_result": result,
                "rollback_available": False,
                "status": status,
                "events": [event],
            },
        )
        self.conversations.update_last_assistant(
            thread_id,
            response,
            agent=updated.get("target_agent"),
            status=status,
            metadata=self._metadata(updated),
        )
        self.audit.record("patch_rollback", thread_id=thread_id, status=result["status"])
        return {"thread_id": thread_id, "status": status, "state": updated}

    @staticmethod
    def _metadata(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "events": state.get("events", []),
            "interrupt": None,
            "action": state.get("proposed_action"),
            "patch_result": state.get("patch_result"),
            "rollback_available": state.get("rollback_available", False),
            "route_confidence": state.get("route_confidence"),
            "route_reason": state.get("route_reason"),
        }

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
