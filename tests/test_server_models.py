import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from juma.server import (
    ApprovalResponse,
    AskResponse,
    HealthResponse,
    HistoryMessage,
    RejectionResponse,
    RollbackResponse,
    ThreadSummary,
    app,
)

client = TestClient(app)


def test_every_endpoint_declares_an_explicit_response_model() -> None:
    response_models = {
        (route.path, tuple(sorted(route.methods or ()))): route.response_model
        for route in app.routes
        if route.path in {
            "/ask",
            "/threads",
            "/threads/{thread_id}/history",
            "/threads/{thread_id}/approve",
            "/threads/{thread_id}/reject",
            "/threads/{thread_id}/rollback",
            "/health",
        }
    }

    assert response_models == {
        ("/ask", ("POST",)): AskResponse,
        ("/threads", ("GET",)): list[ThreadSummary],
        ("/threads/{thread_id}/history", ("GET",)): list[HistoryMessage],
        ("/threads/{thread_id}/approve", ("POST",)): ApprovalResponse,
        ("/threads/{thread_id}/reject", ("POST",)): RejectionResponse,
        ("/threads/{thread_id}/rollback", ("POST",)): RollbackResponse,
        ("/health", ("GET",)): HealthResponse,
    }


def test_run_response_models_validate_runtime_envelopes() -> None:
    payload = {
        "thread_id": "thread-1",
        "status": "completed",
        "state": {"response": "done"},
    }

    assert AskResponse.model_validate(payload).thread_id == "thread-1"
    assert ApprovalResponse.model_validate(payload).status == "completed"
    assert RejectionResponse.model_validate(payload).state == {"response": "done"}
    assert RollbackResponse.model_validate(payload).interrupts is None


def test_response_models_reject_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        AskResponse.model_validate({"thread_id": "thread-1", "status": "completed"})

    with pytest.raises(ValidationError):
        ThreadSummary.model_validate({"thread_id": "thread-1", "title": "A thread"})

    with pytest.raises(ValidationError):
        HistoryMessage.model_validate(
            {
                "id": 1,
                "thread_id": "thread-1",
                "role": "assistant",
                "content": "hello",
                "metadata": {},
            }
        )


def test_health_response_model_accepts_only_a_valid_shape() -> None:
    assert HealthResponse.model_validate({"status": "ok"}).status == "ok"

    with pytest.raises(ValidationError):
        HealthResponse.model_validate({})


def test_ask_rejects_missing_request() -> None:
    response = client.post("/ask", json={})

    assert response.status_code == 422
    assert "request" in response.text


@pytest.mark.parametrize("limit", [0, 201])
def test_thread_list_rejects_invalid_limits(limit: int) -> None:
    response = client.get(f"/threads?limit={limit}")

    assert response.status_code == 422


@pytest.mark.parametrize("limit", [0, 101])
def test_history_rejects_invalid_limits(limit: int) -> None:
    response = client.get(f"/threads/thread-1/history?limit={limit}")

    assert response.status_code == 422


def test_approval_rejects_invalid_fingerprint_type() -> None:
    response = client.post(
        "/threads/thread-1/approve",
        json={"action_fingerprint": {"unexpected": "object"}},
    )

    assert response.status_code == 422
