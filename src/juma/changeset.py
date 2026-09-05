"""Immutable, fingerprinted change contracts shared by API and executors."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ActionKind = Literal[
    "code.patch",
    "filesystem.delete",
    "check.run",
    "package.patch",
    "command.run",
]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    kind: ActionKind
    summary: str = Field(min_length=1, max_length=500)
    risk: Literal["low", "medium", "high"] = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[dict[str, Any]] = Field(default_factory=list)


class GuardrailReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pass", "warn", "block"]
    checks: list[str] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)


class Changeset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, frozen=True)
    changeset_id: uuid.UUID = Field(default_factory=uuid.uuid4, frozen=True)
    run_id: uuid.UUID
    thread_id: uuid.UUID
    workspace_id: str = Field(min_length=1, max_length=200)
    intent: str = Field(min_length=1, max_length=4000)
    risk: Literal["low", "medium", "high"] = "medium"
    policy_version: str = "1"
    actions: list[Action] = Field(min_length=1)
    post_checks: list[str] = Field(default_factory=lambda: ["tests"])
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    base_git_tree: str | None = None
    expected_file_hashes: dict[str, str] = Field(default_factory=dict)
    guardrail_report: GuardrailReportModel | None = None
    status: str = "draft"
    fingerprint: str | None = None
    supersedes: uuid.UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    outcomes: list[dict[str, Any]] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        if self.fingerprint is None:
            object.__setattr__(self, "fingerprint", self.compute_fingerprint())

    @field_validator("fingerprint")
    @classmethod
    def _fingerprint_shape(cls, value: str | None) -> str | None:
        if value is not None and (len(value) != 64 or value.lower() != value):
            raise ValueError("fingerprint must be a lowercase 64-character SHA-256 digest")
        return value

    def canonical_payload(self) -> dict[str, Any]:
        # Timestamps, status, execution outcomes, and presentation-only fields do
        # not participate in authorization. Exact patch bytes and action order do.
        return {
            "schema_version": self.schema_version,
            "changeset_id": str(self.changeset_id),
            "run_id": str(self.run_id),
            "thread_id": str(self.thread_id),
            "workspace_id": self.workspace_id,
            "intent": self.intent,
            "risk": self.risk,
            "policy_version": self.policy_version,
            "actions": [action.model_dump(mode="json") for action in self.actions],
            "post_checks": self.post_checks,
            "preconditions": self.preconditions,
            "base_git_tree": self.base_git_tree,
            "expected_file_hashes": dict(sorted(self.expected_file_hashes.items())),
        }

    def compute_fingerprint(self) -> str:
        encoded = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def with_fingerprint(self) -> Changeset:
        return self.model_copy(update={"fingerprint": self.compute_fingerprint()})
