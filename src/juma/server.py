"""HTTP server for the juma runtime."""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi import Path as FastAPIPath
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, ConfigDict, Field

from dataclasses import replace

from .config import Settings
from .service import Juma


class AskRequest(BaseModel):
    """Payload accepted by the ask endpoint."""

    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=50_000)
    thread_id: str | None = None
    workspace_id: str | None = Field(default=None, max_length=200)


class ActionRequest(BaseModel):
    """Payload accepted when approving or rejecting an action."""

    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(default="", max_length=10_000)
    action_fingerprint: str | None = None


class PreferenceUpdate(BaseModel):
    """Payload used to create or replace a user preference."""

    model_config = ConfigDict(extra="forbid")

    value: str = Field(max_length=4000)


class PreferenceResponse(BaseModel):
    """A durable user preference."""

    key: str
    value: str


class MemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crew: str = Field(min_length=1, max_length=40)
    content: str = Field(min_length=1, max_length=20_000)
    scope: str = Field(default="shared", max_length=40)


class MemoryResponse(BaseModel):
    id: int
    stored: bool


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


class VersionedRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    request: str = Field(min_length=1, max_length=50_000)
    thread_id: str | None = Field(default=None, max_length=200)
    workspace_id: str | None = Field(default=None, max_length=200)


class RunAccepted(BaseModel):
    run_id: str
    thread_id: str
    status: str
    schema_version: int = 1


class VersionedRunStatus(BaseModel):
    run_id: str
    thread_id: str
    status: str
    state: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    feedback: str = Field(default="", max_length=10_000)
    action_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class RunCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="cancelled by operator", max_length=500)


class SuggestionAccepted(BaseModel):
    run_id: str
    suggestion_id: str
    status: str


class _RunRegistry:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="juma-run")
        self.runs: dict[str, dict[str, Any]] = {}
        self.idempotency: dict[str, tuple[str, str]] = {}

    def create(self, payload: VersionedRunRequest, idem: str | None) -> dict[str, Any]:
        with self.lock:
            request_digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
            if idem and idem in self.idempotency:
                existing_id, existing_digest = self.idempotency[idem]
                if existing_digest != request_digest:
                    raise ValueError("Idempotency-Key was already used for a different request.")
                return self.runs[existing_id]
            run_id = str(uuid.uuid4())
            thread_id = payload.thread_id or str(uuid.uuid4())
            now = datetime.now(UTC).isoformat()
            item = {
                "run_id": run_id,
                "thread_id": thread_id,
                "request": payload.request,
                "workspace_id": payload.workspace_id,
                "status": "queued",
                "state": {},
                "events": [{"event": "queued", "at": now}],
                "created_at": now,
                "updated_at": now,
            }
            self.runs[run_id] = item
            if idem:
                self.idempotency[idem] = (run_id, request_digest)
            self.executor.submit(self._execute, run_id)
            return item

    def _execute(self, run_id: str) -> None:
        with self.lock:
            item = self.runs.get(run_id)
            if not item or item["status"] == "cancelled":
                return
            item["status"] = "running"
            item["events"].append({"event": "running", "at": datetime.now(UTC).isoformat()})
            item["updated_at"] = datetime.now(UTC).isoformat()
        try:
            settings = Settings.from_env()
            if item.get("workspace_id"):
                settings = replace(settings, workspace_id=item["workspace_id"])
            with Juma(settings) as juma:
                result = juma.ask(item["request"], thread_id=item["thread_id"], run_id=run_id)
            with self.lock:
                if item["status"] == "cancelled":
                    item["events"].append({"event": "completed_after_cancel", "at": datetime.now(UTC).isoformat()})
                    item["updated_at"] = datetime.now(UTC).isoformat()
                    return
                item["state"] = result.get("state", {})
                item["status"] = {"completed": "succeeded"}.get(result.get("status"), result.get("status", "failed"))
                item["events"].extend(item["state"].get("events", []))
                item["updated_at"] = datetime.now(UTC).isoformat()
        except Exception as exc:
            with self.lock:
                item["status"] = "failed"
                item["state"] = {"error": str(exc)}
                item["events"].append({"event": "failed", "error": type(exc).__name__})
                item["updated_at"] = datetime.now(UTC).isoformat()


registry = _RunRegistry()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        max_bytes = Settings.from_env().api_max_body_bytes
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > max_bytes:
            return Response("Request body too large", status_code=413)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


app = FastAPI(title="juma")
app.add_middleware(SecurityHeadersMiddleware)


def require_api_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Require a valid bearer token for protected API routes."""
    configured_token = os.getenv("JUMA_API_TOKEN")
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured.",
        )

    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail="Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, supplied_token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not supplied_token
        or not hmac.compare_digest(supplied_token, configured_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _bad_request(error: ValueError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@app.post(
    "/ask",
    response_model=AskResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(require_api_token)],
)
def ask(payload: AskRequest) -> AskResponse:
    """Run a request through Juma and return its result envelope."""
    with Juma() as juma:
        return AskResponse.model_validate(juma.ask(payload.request, thread_id=payload.thread_id))


@app.get(
    "/threads",
    response_model=list[ThreadSummary],
    dependencies=[Depends(require_api_token)],
)
def list_threads(
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ThreadSummary]:
    """List the most recently updated conversation threads."""
    with Juma() as juma:
        return [ThreadSummary.model_validate(thread) for thread in juma.threads(limit=limit)]


@app.get(
    "/threads/{thread_id}/history",
    response_model=list[HistoryMessage],
    dependencies=[Depends(require_api_token)],
)
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
    dependencies=[Depends(require_api_token)],
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
    dependencies=[Depends(require_api_token)],
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
    dependencies=[Depends(require_api_token)],
)
def rollback_thread(thread_id: str, payload: ActionRequest) -> RollbackResponse:
    """Roll back a failed patch after verifying its exact fingerprint."""
    try:
        with Juma() as juma:
            return RollbackResponse.model_validate(
                juma.rollback(
                    thread_id,
                    action_fingerprint=payload.action_fingerprint,
                )
            )
    except ValueError as error:
        raise _bad_request(error) from error


@app.get(
    "/preferences",
    response_model=list[PreferenceResponse],
    dependencies=[Depends(require_api_token)],
)
def list_preferences() -> list[PreferenceResponse]:
    """Return the preferences currently used by Juma."""
    with Juma() as juma:
        return [
            PreferenceResponse(key=key, value=value)
            for key, value in juma.preference_values().items()
        ]


@app.post("/memories", response_model=MemoryResponse, dependencies=[Depends(require_api_token)])
def create_memory(payload: MemoryRequest) -> MemoryResponse:
    runtime = Juma()
    if payload.workspace_id:
        runtime = Juma(replace(Settings.from_env(), workspace_id=payload.workspace_id))
    with runtime as juma:
        return MemoryResponse(id=juma.remember(payload.crew, payload.content, scope=payload.scope), stored=True)


@app.get("/memories", dependencies=[Depends(require_api_token)])
def search_memories(query: str = Query(default="", max_length=500)) -> dict[str, Any]:
    with Juma() as juma:
        return {"memories": juma.memory.search(query, scope="shared", limit=8) if query else juma.memory.recent(scope="shared", limit=8)}


@app.get("/context", dependencies=[Depends(require_api_token)])
def get_context(
    query: str = Query(default="", max_length=2_000),
    workspace_id: str = Query(default="default", min_length=1, max_length=200),
    thread_id: str = Query(default="default", min_length=1, max_length=200),
    crew: str = Query(default="coding", min_length=1, max_length=40),
    token_budget: int = Query(default=1200, ge=128, le=12_000),
) -> dict[str, Any]:
    with Juma() as juma:
        return juma.context.get_context(query, workspace_id, thread_id, crew, token_budget)


@app.put(
    "/preferences/{key}",
    response_model=PreferenceResponse,
    dependencies=[Depends(require_api_token)],
)
def update_preference(
    key: Annotated[str, FastAPIPath(min_length=1, max_length=80)],
    payload: PreferenceUpdate,
) -> PreferenceResponse:
    """Create or replace one durable user preference."""
    try:
        with Juma() as juma:
            return PreferenceResponse.model_validate(juma.set_preference(key, payload.value))
    except ValueError as error:
        raise _bad_request(error) from error


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Report whether the HTTP service is available."""
    return HealthResponse(status="ok")


@app.get("/ready", response_model=HealthResponse)
def ready() -> HealthResponse:
    """Readiness probe used by Caddy/orchestration after migrations."""
    settings = Settings.from_env()
    try:
        settings.ensure_directories()
        return HealthResponse(status="ready")
    except OSError as exc:
        raise HTTPException(status_code=503, detail=f"not ready: {exc}") from exc


@app.post("/v1/runs", response_model=RunAccepted, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_token)])
def create_run(payload: VersionedRunRequest, idempotency_key: Annotated[str | None, Header()] = None) -> RunAccepted:
    if idempotency_key and len(idempotency_key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key is too long.")
    try:
        item = registry.create(payload, idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RunAccepted(run_id=item["run_id"], thread_id=item["thread_id"], status=item["status"])


def _run_or_404(run_id: str) -> dict[str, Any]:
    with registry.lock:
        item = registry.runs.get(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return item


def _settings_for_run(item: dict[str, Any]) -> Settings:
    settings = Settings.from_env()
    return replace(settings, workspace_id=item["workspace_id"]) if item.get("workspace_id") else settings


@app.get("/v1/runs/{run_id}", response_model=VersionedRunStatus, dependencies=[Depends(require_api_token)])
def get_run(run_id: str) -> VersionedRunStatus:
    item = _run_or_404(run_id)
    return VersionedRunStatus.model_validate(item)


@app.get("/v1/runs/{run_id}/events", dependencies=[Depends(require_api_token)])
def run_events(
    run_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    item = _run_or_404(run_id)
    with registry.lock:
        events = list(item["events"])
    try:
        offset = max(0, int(last_event_id or "0"))
    except ValueError:
        offset = 0
    body = "".join(f"id: {index}\ndata: {json.dumps(event)}\n\n" for index, event in enumerate(events[offset:], offset + 1))
    return StreamingResponse(iter([body]), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/v1/runs/{run_id}/decision", response_model=VersionedRunStatus, dependencies=[Depends(require_api_token)])
def decide_run(run_id: str, payload: DecisionRequest) -> VersionedRunStatus:
    item = _run_or_404(run_id)
    if item["status"] not in {"waiting_approval", "rejected", "failed"}:
        raise HTTPException(status_code=409, detail="Run is not awaiting a decision.")
    try:
        with Juma(_settings_for_run(item)) as juma:
            result = juma.resume(item["thread_id"], approved=payload.approved, feedback=payload.feedback, action_fingerprint=payload.action_fingerprint)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with registry.lock:
        item["state"] = result.get("state", {})
        item["status"] = {"completed": "succeeded"}.get(result.get("status"), result.get("status", "failed"))
        item["events"].extend(item["state"].get("events", []))
        item["updated_at"] = datetime.now(UTC).isoformat()
    return VersionedRunStatus.model_validate(item)


@app.post("/v1/runs/{run_id}/cancel", response_model=VersionedRunStatus, dependencies=[Depends(require_api_token)])
def cancel_run(run_id: str, payload: RunCancelRequest) -> VersionedRunStatus:
    item = _run_or_404(run_id)
    with registry.lock:
        if item["status"] in {"succeeded", "completed", "failed", "cancelled"}:
            raise HTTPException(status_code=409, detail="Run cannot be cancelled in its current state.")
        item["status"] = "cancelled"
        item["events"].append({"event": "cancelled", "reason": payload.reason})
        item["updated_at"] = datetime.now(UTC).isoformat()
    return VersionedRunStatus.model_validate(item)


@app.post("/v1/runs/{run_id}/rollback", response_model=VersionedRunStatus, dependencies=[Depends(require_api_token)])
def rollback_run(run_id: str, payload: ActionRequest) -> VersionedRunStatus:
    item = _run_or_404(run_id)
    try:
        with Juma(_settings_for_run(item)) as juma:
            result = juma.rollback(item["thread_id"], action_fingerprint=payload.action_fingerprint)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    with registry.lock:
        item["state"] = result.get("state", {})
        item["status"] = {"completed": "succeeded"}.get(result.get("status"), result.get("status", "failed"))
        item["events"].extend(item["state"].get("events", []))
        item["updated_at"] = datetime.now(UTC).isoformat()
    return VersionedRunStatus.model_validate(item)


@app.post("/v1/runs/{run_id}/suggestions/{suggestion_id}/accept", response_model=SuggestionAccepted, dependencies=[Depends(require_api_token)])
def accept_suggestion(run_id: str, suggestion_id: str) -> SuggestionAccepted:
    """Accepting a suggestion creates a child run; it never executes directly."""
    item = _run_or_404(run_id)
    request_text = str(item.get("request") or "Follow up on the completed run")
    child = registry.create(VersionedRunRequest(request=request_text), None)
    return SuggestionAccepted(run_id=child["run_id"], suggestion_id=suggestion_id, status=child["status"])


def main() -> None:
    """Run the juma HTTP server locally."""
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run("juma.server:app", host=settings.server_host, port=settings.server_port)
