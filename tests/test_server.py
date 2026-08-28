from fastapi.testclient import TestClient

from juma import server

client = TestClient(server.app)


class FakeJuma:
    calls: list[tuple[str, str | None]] = []

    def __enter__(self) -> "FakeJuma":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def ask(self, request: str, *, thread_id: str | None = None) -> dict:
        self.calls.append((request, thread_id))
        return {
            "thread_id": thread_id or "generated-thread",
            "status": "completed",
            "state": {"response": "fake result"},
        }


def test_health_returns_ok() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_forwards_request_and_thread_id(monkeypatch) -> None:
    FakeJuma.calls.clear()
    monkeypatch.setattr(server, "Juma", FakeJuma)

    response = client.post("/ask", json={"request": "hello", "thread_id": "thread-1"})

    assert response.status_code == 200
    assert response.json() == {
        "thread_id": "thread-1",
        "status": "completed",
        "state": {"response": "fake result"},
    }
    assert FakeJuma.calls == [("hello", "thread-1")]


def test_ask_allows_omitted_thread_id(monkeypatch) -> None:
    FakeJuma.calls.clear()
    monkeypatch.setattr(server, "Juma", FakeJuma)

    response = client.post("/ask", json={"request": "start a new conversation"})

    assert response.status_code == 200
    assert response.json()["thread_id"] == "generated-thread"
    assert FakeJuma.calls == [("start a new conversation", None)]
