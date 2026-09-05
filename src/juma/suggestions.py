"""Inert, typed follow-up suggestions. Accepting one always creates a new run."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Suggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    suggestion_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: Literal["depth", "breadth", "recovery"]
    title: str = Field(min_length=1, max_length=200)
    request: str = Field(min_length=1, max_length=4000)
    source_run_id: uuid.UUID
    workspace_id: str
    status: Literal["inert", "accepted", "dismissed"] = "inert"


def build_suggestions(*, run_id: str, workspace_id: str, status: str, request: str) -> list[dict[str, Any]]:
    try:
        source = uuid.UUID(run_id)
    except ValueError:
        source = uuid.uuid5(uuid.NAMESPACE_URL, run_id)
    if status in {"succeeded", "completed"}:
        kinds = [("depth", "Verify the result", f"Run focused checks for: {request}"), ("breadth", "Explore a follow-up", f"Find adjacent improvements related to: {request}")]
    elif status in {"failed", "rollback_failed"}:
        kinds = [("recovery", "Review the failure", f"Inspect the failed run and propose a safe recovery for: {request}")]
    else:
        kinds = []
    return [Suggestion(kind=kind, title=title, request=text, source_run_id=source, workspace_id=workspace_id).model_dump(mode="json") for kind, title, text in kinds[:2]]
