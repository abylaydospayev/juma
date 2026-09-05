from __future__ import annotations

import hmac
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from threading import Lock
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from .audit import AuditLog
from .config import Settings
from .conversation import ConversationStore
from .context import ContextService
from .changeset_store import ChangesetStore
from .changeset import Changeset
from .journal import ExecutorJournal
from .graph import build_graph
from .locking import CrossProcessLock, LockBusyError
from .memory import MemoryStore
from .models import ModelClient, OpenAIResponsesModel
from .patches import PatchManager
from .preferences import PreferenceStore
from .outcomes import OutcomeStore
from .router import route_request


class Juma:
    _process_locks: dict[str, Lock] = {}
    _process_locks_guard = Lock()

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
        self.preferences = PreferenceStore(self.settings.preferences_db)
        self.context = ContextService(self.memory, self.preferences)
        self.outcomes = OutcomeStore(self.settings.data_dir / "outcomes.sqlite")
        self.changesets = ChangesetStore(self.settings.data_dir / "changesets.sqlite")
        self.journal = ExecutorJournal(self.settings.data_dir / "executor-journal.sqlite")
        self.audit = AuditLog(self.settings.audit_log)
        self.patch_manager = PatchManager(
            self.settings.resolved_workspace_root,
            auto_setup_environment=self.settings.auto_setup_environment,
            environment_timeout=self.settings.environment_timeout,
        )
        self.graph = build_graph(
            self.checkpointer,
            self.model,
            self.memory,
            self.patch_manager,
            settings=self.settings,
            context=self.context,
            journal=self.journal,
        )

    @staticmethod
    def _config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    @classmethod
    @contextmanager
    def _guard(cls, keys: list[str]):
        normalized = sorted(set(keys))
        local_locks: list[Lock] = []
        file_locks: list[CrossProcessLock] = []
        try:
            with cls._process_locks_guard:
                for key in normalized:
                    lock = cls._process_locks.setdefault(key, Lock())
                    if not lock.acquire(blocking=False):
                        raise RuntimeError(f"A Juma operation is already active for {key}.")
                    local_locks.append(lock)
            for key in normalized:
                file_lock = CrossProcessLock(key)
                file_lock.acquire()
                file_locks.append(file_lock)
            yield
        except LockBusyError as exc:
            raise RuntimeError("A Juma operation is already active for this resource.") from exc
        finally:
            for file_lock in reversed(file_locks):
                file_lock.release()
            for lock in reversed(local_locks):
                lock.release()

    @classmethod
    @contextmanager
    def _workspace_guard(cls, workspace: Any):
        with cls._guard([f"workspace:{Path(workspace).resolve()}"]):
            yield

    def _execution_guard(self, thread_id: str):
        return self._guard([f"workspace:{self.patch_manager.root}", f"thread:{thread_id}"])

    def _thread_guard(self, thread_id: str):
        return self._guard([f"thread:{thread_id}"])

    def _request_guard(self, request: str, thread_id: str):
        """Avoid serializing unrelated research/admin work on the coding workspace."""
        if route_request(request)["target_agent"] == "coding":
            return self._execution_guard(thread_id)
        return self._thread_guard(thread_id)

    def ask(
        self,
        request: str,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        thread_id = thread_id or str(uuid.uuid4())
        run_id = run_id or str(uuid.uuid4())
        with self._request_guard(request, thread_id):
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
                        "user_preferences": self.preferences.all(),
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "workspace_id": self.settings.workspace_id,
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
            if result.get("changeset"):
                try:
                    persisted = self.changesets.create(Changeset.model_validate(result["changeset"]))
                    result["changeset"] = persisted.model_dump(mode="json")
                except (ValueError, TypeError):
                    # The legacy action remains available for compatibility even
                    # if a malformed optional changeset cannot be indexed.
                    self.audit.record("changeset_persist_failed", thread_id=thread_id)
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
            self.outcomes.record(
                run_id=run_id,
                thread_id=thread_id,
                workspace_id=self.settings.workspace_id,
                result_code=envelope["status"],
                crew=result.get("target_agent"),
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
        proposed = dict(snapshot.values).get("proposed_action") or {}
        guard = (
            self._execution_guard(thread_id)
            if proposed.get("kind") == "code.patch"
            else self._thread_guard(thread_id)
        )
        with guard:
            snapshot = self.graph.get_state(self._config(thread_id))
            interrupts = [
                item
                for task in snapshot.tasks
                for item in getattr(task, "interrupts", ())
            ]
            state = dict(snapshot.values)
            if not interrupts:
                if self._is_idempotent_approval(state, approved, feedback, action_fingerprint):
                    return self._envelope_from_state(thread_id, state)
                raise ValueError(f"Thread {thread_id!r} is not waiting for approval.")
            pending_action = state.get("proposed_action") or {}
            if approved and pending_action.get("kind") in {
                "code.patch",
                "filesystem.delete",
                "check.run",
            }:
                expected_fingerprint = pending_action.get("fingerprint")
                if not (
                    isinstance(action_fingerprint, str)
                    and isinstance(expected_fingerprint, str)
                    and hmac.compare_digest(action_fingerprint, expected_fingerprint)
                ):
                    raise ValueError(
                        "This approval requires the exact action fingerprint shown in the preview."
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
            self.outcomes.record(
                run_id=str(state.get("run_id") or uuid.uuid5(uuid.NAMESPACE_URL, thread_id)),
                thread_id=thread_id,
                workspace_id=self.settings.workspace_id,
                result_code=envelope["status"],
                crew=result.get("target_agent"),
                usage=getattr(self.model, "last_usage", {}),
            )
            return envelope

    @staticmethod
    def _is_idempotent_approval(
        state: dict[str, Any],
        approved: bool,
        feedback: str,
        action_fingerprint: str | None,
    ) -> bool:
        approval = state.get("approval") or {}
        if not approval or bool(approval.get("approved")) != approved:
            return False
        if str(approval.get("feedback", "")) != feedback:
            return False
        expected = approval.get("action_fingerprint")
        if expected is None:
            return action_fingerprint is None
        action = state.get("proposed_action") or {}
        if action_fingerprint is None and action.get("kind") != "code.patch":
            return True
        return (
            isinstance(action_fingerprint, str)
            and isinstance(expected, str)
            and hmac.compare_digest(action_fingerprint, expected)
        )

    @staticmethod
    def _envelope_from_state(thread_id: str, state: dict[str, Any]) -> dict[str, Any]:
        return {"thread_id": thread_id, "status": state["status"], "state": state}

    def history(self, thread_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.conversations.history(thread_id, limit=limit)

    def threads(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.conversations.list_threads(limit=limit)

    def remember(self, crew: str, content: str, *, scope: str = "shared") -> int:
        return self.memory.remember(crew, content, scope=scope)

    def set_preference(self, key: str, value: str) -> dict[str, str]:
        return self.preferences.set(key, value)

    def preference_values(self) -> dict[str, str]:
        return self.preferences.all()

    def rollback(
        self,
        thread_id: str,
        *,
        action_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        with self._execution_guard(thread_id):
            snapshot = self.graph.get_state(self._config(thread_id))
            state = dict(snapshot.values)
            action = state.get("proposed_action") or {}
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
            if patch_result.get("status") == "rolled_back":
                return self._envelope_from_state(thread_id, state)
            patch = action.get("patch")
            if not state.get("rollback_available") or not patch:
                raise ValueError("No failed patch is available to roll back for this thread.")
            result = self.patch_manager.rollback(
                patch,
                expected_post_apply_hashes=patch_result.get("post_apply_hashes"),
                expected_pre_apply_hashes=patch_result.get("pre_apply_hashes"),
            )
            if result["status"] == "rolled_back":
                response = (
                    state["response"]
                    + " The patch was rolled back and the tests were rerun."
                )
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
            "changeset": state.get("changeset"),
            "patch_result": state.get("patch_result"),
            "rollback_available": state.get("rollback_available", False),
            "route_confidence": state.get("route_confidence"),
            "route_reason": state.get("route_reason"),
            "plan": state.get("plan", []),
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
        self.preferences.close()
        self.outcomes.close()
        self.changesets.close()
        self.journal.close()
        self._checkpoint_connection.close()

    def __enter__(self) -> Juma:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
