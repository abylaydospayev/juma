from pathlib import Path

from fastapi.testclient import TestClient

from juma import server
from juma.config import Settings
from juma.service import Juma as ServiceJuma

client = TestClient(server.app)


class FakeJuma:
    calls: list[tuple[str, tuple, dict]] = []

    def __enter__(self) -> "FakeJuma":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def ask(self, request: str, *, thread_id: str | None = None) -> dict:
        self.calls.append(("ask", (request,), {"thread_id": thread_id}))
        return {
            "thread_id": thread_id or "generated-thread",
            "status": "completed",
            "state": {"response": "fake result"},
        }

    def threads(self, *, limit: int = 50) -> list[dict]:
        self.calls.append(("threads", (), {"limit": limit}))
        return [{"thread_id": "thread-1", "title": "hello", "updated_at": "now"}]

    def history(self, thread_id: str, *, limit: int = 100) -> list[dict]:
        self.calls.append(("history", (thread_id,), {"limit": limit}))
        return [{"thread_id": thread_id, "role": "user", "content": "hello"}]

    def resume(
        self,
        thread_id: str,
        *,
        approved: bool,
        feedback: str = "",
        action_fingerprint: str | None = None,
    ) -> dict:
        self.calls.append(
            (
                "resume",
                (thread_id,),
                {
                    "approved": approved,
                    "feedback": feedback,
                    "action_fingerprint": action_fingerprint,
                },
            )
        )
        if thread_id == "missing":
            raise ValueError("Thread 'missing' is not waiting for approval.")
        return {
            "thread_id": thread_id,
            "status": "completed",
            "state": {"approved": approved, "feedback": feedback},
        }

    def rollback(self, thread_id: str) -> dict:
        self.calls.append(("rollback", (thread_id,), {}))
        return {"thread_id": thread_id, "status": "completed", "state": {}}


def install_fake(monkeypatch) -> None:
    FakeJuma.calls.clear()
    monkeypatch.setattr(server, "Juma", FakeJuma)


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_forwards_request_and_thread_id(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.post("/ask", json={"request": "hello", "thread_id": "thread-1"})

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "completed",
        "state": {"response": "fake result"},
    }
    assert FakeJuma.calls == [("ask", ("hello",), {"thread_id": "thread-1"})]


def test_ask_allows_omitted_thread_id(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.post("/ask", json={"request": "start a new conversation"})

    assert response.status_code == 200
    assert response.json()["thread_id"] == "generated-thread"
    assert FakeJuma.calls == [("ask", ("start a new conversation",), {"thread_id": None})]


def test_list_threads_forwards_limit(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.get("/threads?limit=10")

    assert response.status_code == 200
    assert response.json() == [
        {"thread_id": "thread-1", "title": "hello", "updated_at": "now"}
    ]
    assert FakeJuma.calls == [("threads", (), {"limit": 10})]


def test_thread_history_forwards_thread_and_limit(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.get("/threads/thread-1/history?limit=25")

    assert response.status_code == 200
    assert response.json() == [{"thread_id": "thread-1", "role": "user", "content": "hello"}]
    assert FakeJuma.calls == [("history", ("thread-1",), {"limit": 25})]


def test_approve_forwards_feedback_and_fingerprint(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.post(
        "/threads/thread-1/approve",
        json={"feedback": "Looks good", "action_fingerprint": "abc123"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == {"approved": True, "feedback": "Looks good"}
    assert FakeJuma.calls == [
        (
            "resume",
            ("thread-1",),
            {"approved": True, "feedback": "Looks good", "action_fingerprint": "abc123"},
        )
    ]


def test_reject_forwards_feedback(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.post("/threads/thread-1/reject", json={"feedback": "Needs revision"})

    assert response.status_code == 200
    assert response.json()["state"] == {"approved": False, "feedback": "Needs revision"}
    assert FakeJuma.calls == [
        (
            "resume",
            ("thread-1",),
            {"approved": False, "feedback": "Needs revision", "action_fingerprint": None},
        )
    ]


def test_rollback_forwards_thread_id(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.post("/threads/thread-1/rollback")

    assert response.status_code == 200
    assert response.json() == {"thread_id": "thread-1", "status": "completed", "state": {}}
    assert FakeJuma.calls == [("rollback", ("thread-1",), {})]


def test_pending_action_error_returns_bad_request(monkeypatch) -> None:
    install_fake(monkeypatch)

    response = client.post("/threads/missing/approve", json={})

    assert response.status_code == 400
    assert response.json() == {"detail": "Thread 'missing' is not waiting for approval."}


class IntegrationFakeModel:
    def route(self, request: str) -> dict[str, str | float]:
        return {
            "target_agent": "research",
            "confidence": 1.0,
            "reason": "Deterministic integration-test route.",
        }

    def generate(self, crew, request, *, proposed_action=None) -> str:
        return f"{crew} answer: {request}"


def test_thread_lifecycle_persists_across_http_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(
        data_dir=tmp_path,
        checkpoint_db=tmp_path / "checkpoints.sqlite",
        memory_db=tmp_path / "memory.sqlite",
    )

    def runtime() -> ServiceJuma:
        return ServiceJuma(settings, model=IntegrationFakeModel())

    monkeypatch.setattr(server, "Juma", runtime)

    ask_response = client.post(
        "/ask",
        json={"request": "research durable agent systems"},
    )

    assert ask_response.status_code == 200
    result = ask_response.json()
    thread_id = result["thread_id"]
    assert thread_id
    assert result["status"] == "completed"

    history_response = client.get(f"/threads/{thread_id}/history")

    assert history_response.status_code == 200
    history = history_response.json()
    assert [message["role"] for message in history] == ["user", "assistant"]
    assert history[0]["thread_id"] == thread_id
    assert history[0]["content"] == "research durable agent systems"
    assert history[1]["thread_id"] == thread_id
    assert history[1]["content"] == (
        "research answer: research durable agent systems"
    )

    threads_response = client.get("/threads")

    assert threads_response.status_code == 200
    threads = threads_response.json()
    assert any(thread["thread_id"] == thread_id for thread in threads)
