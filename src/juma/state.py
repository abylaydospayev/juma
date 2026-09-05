from __future__ import annotations

import operator
from typing import Annotated, Literal, NotRequired, TypedDict

AgentName = Literal["coding", "research", "admin"]
RunStatus = Literal[
    "queued",
    "running",
    "routing",
    "working",
    "waiting_approval",
    "completed",
    "succeeded",
    "cancelled",
    "rejected",
    "failed",
]


class Event(TypedDict):
    source: str
    message: str


class ProposedAction(TypedDict):
    kind: str
    summary: str
    risk: Literal["medium", "high"]
    parameters: NotRequired[dict]
    fingerprint: NotRequired[str]
    changeset_fingerprint: NotRequired[str]
    patch: NotRequired[str]


class PatchResult(TypedDict):
    status: Literal[
        "applied_tests_passed",
        "applied_tests_failed",
        "apply_failed",
        "rolled_back",
        "rollback_failed",
    ]
    files: list[str]
    pre_apply_hashes: NotRequired[dict[str, str | None]]
    post_apply_hashes: NotRequired[dict[str, str | None]]
    restored_hashes: NotRequired[dict[str, str | None]]
    repair_attempts: NotRequired[list[dict]]
    branch: NotRequired[str]
    commit: NotRequired[dict]
    push: NotRequired[dict]
    test: NotRequired[dict]
    error: NotRequired[str]
    rollback: NotRequired[dict]


class Approval(TypedDict):
    approved: bool
    feedback: str
    action_fingerprint: NotRequired[str | None]


class ConversationMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str
    agent: NotRequired[AgentName | None]
    status: NotRequired[str]


class JumaState(TypedDict):
    run_id: NotRequired[str]
    thread_id: NotRequired[str]
    workspace_id: NotRequired[str]
    request: str
    target_agent: NotRequired[AgentName]
    route_confidence: NotRequired[float]
    route_reason: NotRequired[str]
    plan: NotRequired[list[str]]
    user_preferences: NotRequired[dict[str, str]]
    conversation_history: NotRequired[list[ConversationMessage]]
    memory_context: NotRequired[list[dict]]
    context_bundle: NotRequired[dict]
    response: NotRequired[str]
    proposed_action: NotRequired[ProposedAction | None]
    changeset: NotRequired[dict | None]
    patch_result: NotRequired[PatchResult | None]
    rollback_available: NotRequired[bool]
    approval: NotRequired[Approval | None]
    status: RunStatus
    events: Annotated[list[Event], operator.add]
