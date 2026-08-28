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


app = FastAPI(title="juma")


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@app.post("/ask")
def ask(payload: AskRequest) -> dict:
    """Run a request through Juma and return its result envelope."""
    with Juma() as juma:
        return juma.ask(payload.request, thread_id=payload.thread_id)


@app.get("/threads")
def list_threads(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """List the most recently updated conversation threads."""
    with Juma() as juma:
        return juma.threads(limit=limit)


@app.get("/threads/{thread_id}/history")
def thread_history(
    thread_id: str,
    limit: int = Query(default=100, ge=1, le=100),
) -> list[dict[str, Any]]:
    """Return the durable message history for a thread."""
    with Juma() as juma:
        return juma.history(thread_id, limit=limit)


@app.post("/threads/{thread_id}/approve")
def approve_thread(thread_id: str, payload: ActionRequest) -> dict[str, Any]:
    """Approve a pending action and resume its thread."""
    try:
        with Juma() as juma:
            return juma.resume(
                thread_id,
                approved=True,
                feedback=payload.feedback,
                action_fingerprint=payload.action_fingerprint,
            )
    except ValueError as error:
        raise _bad_request(error) from error


@app.post("/threads/{thread_id}/reject")
def reject_thread(thread_id: str, payload: ActionRequest) -> dict[str, Any]:
    """Reject a pending action and resume its thread."""
    try:
        with Juma() as juma:
            return juma.resume(
                thread_id,
                approved=False,
                feedback=payload.feedback,
                action_fingerprint=payload.action_fingerprint,
            )
    except ValueError as error:
        raise _bad_request(error) from error


@app.post("/threads/{thread_id}/rollback")
def rollback_thread(thread_id: str) -> dict[str, Any]:
    """Roll back a failed patch for a thread."""
    try:
        with Juma() as juma:
            return juma.rollback(thread_id)
    except ValueError as error:
        raise _bad_request(error) from error


@app.get("/health")
def health() -> dict[str, str]:
    """Report whether the HTTP service is available."""
    return {"status": "ok"}


def main() -> None:
    """Run the juma HTTP server locally."""
    import uvicorn

    uvicorn.run("juma.server:app", host="127.0.0.1", port=8000)
