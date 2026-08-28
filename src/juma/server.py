"""HTTP server for the juma runtime."""

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .service import Juma


class AskRequest(BaseModel):
    """Payload accepted by the ask endpoint."""

    request: str
    thread_id: str | None = None


class ActionRequest(BaseModel):
    """Payload accepted when approving or rejecting an action."""

    feedback: str = ""
    action_fingerprint: str | None = None


class RunResponse(BaseModel):
    """Common result envelope returned by runtime operations."""

    thread_id: str
    status: str
    interrupts: list[Any] | None = None
    state: dict[str, Any]


class AskResponse(RunResponse):
    """Response returned by the ask endpoint."""


class ApprovalResponse(RunResponse):
    """Response returned after approving a thread action."""


class RejectionResponse(RunResponse):
    """Response returned after rejecting a thread action."""


class RollbackResponse(RunResponse):
    """Response returned after rolling back a failed patch."""


class ThreadSummary(BaseModel):
    """Summary of a persisted conversation thread."""

    thread_id: str
    title: str
    updated_at: str


class HistoryMessage(BaseModel):
    """Persisted message in a conversation thread."""

    id: int
    thread_id: str
    role: str
    content: str
    agent: str | None = None
    status: str | None = None
    metadata: dict[str, Any]
    created_at: str


class HealthResponse(BaseModel):
    """Availability response for the health endpoint."""

    status: str


app = FastAPI(title="juma")


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@app.post("/ask", response_model=AskResponse, response_model_exclude_none=True)
def ask(payload: AskRequest) -> AskResponse:
    """Run a request through Juma and return its result envelope."""
    with Juma() as juma:
        return AskResponse.model_validate(juma.ask(payload.request, thread_id=payload.thread_id))


@app.get("/threads", response_model=list[ThreadSummary])
def list_threads(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ThreadSummary]:
    """List the most recently updated conversation threads."""
    with Juma() as juma:
        return [ThreadSummary.model_validate(thread) for thread in juma.threads(limit=limit)]


@app.get("/threads/{thread_id}/history", response_model=list[HistoryMessage])
def thread_history(
    thread_id: str,
    limit: int = Query(default=100, ge=1, le=100),
) -> list[HistoryMessage]:
    """Return the durable message history for a thread."""
    with Juma() as juma:
        return [
            HistoryMessage.model_validate(message)
            for message in juma.history(thread_id, limit=limit)
        ]


@app.post(
    "/threads/{thread_id}/approve",
    response_model=ApprovalResponse,
    response_model_exclude_none=True,
)
def approve_thread(thread_id: str, payload: ActionRequest) -> ApprovalResponse:
    """Approve a pending action and resume its thread."""
    try:
        with Juma() as juma:
            result = juma.resume(
                thread_id,
                approved=True,
                feedback=payload.feedback,
                action_fingerprint=payload.action_fingerprint,
            )
            return ApprovalResponse.model_validate(result)
    except ValueError as error:
        raise _bad_request(error) from error


@app.post(
    "/threads/{thread_id}/reject",
    response_model=RejectionResponse,
    response_model_exclude_none=True,
)
def reject_thread(thread_id: str, payload: ActionRequest) -> RejectionResponse:
    """Reject a pending action and resume its thread."""
    try:
        with Juma() as juma:
            result = juma.resume(
                thread_id,
                approved=False,
                feedback=payload.feedback,
                action_fingerprint=payload.action_fingerprint,
            )
            return RejectionResponse.model_validate(result)
    except ValueError as error:
        raise _bad_request(error) from error


@app.post(
    "/threads/{thread_id}/rollback",
    response_model=RollbackResponse,
    response_model_exclude_none=True,
)
def rollback_thread(thread_id: str) -> RollbackResponse:
    """Roll back a failed patch for a thread."""
    try:
        with Juma() as juma:
            return RollbackResponse.model_validate(juma.rollback(thread_id))
    except ValueError as error:
        raise _bad_request(error) from error


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report whether the HTTP service is available."""
    return HealthResponse(status="ok")


def main() -> None:
    """Run the juma HTTP server locally."""
    import uvicorn

    uvicorn.run("juma.server:app", host="127.0.0.1", port=8000)
