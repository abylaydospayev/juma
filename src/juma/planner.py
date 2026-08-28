"""Deterministic planning for Juma's bounded command loop."""

from __future__ import annotations

from .state import AgentName

_CHANGE_TERMS = {
    "add",
    "build",
    "change",
    "create",
    "edit",
    "fix",
    "implement",
    "modify",
    "refactor",
    "update",
}


def build_plan(request: str, target_agent: AgentName) -> list[str]:
    """Return a short, inspectable plan without making an external model call."""
    lowered = request.casefold()
    if target_agent == "coding":
        if set(lowered.replace("/", " ").split()) & _CHANGE_TERMS:
            return [
                "Inspect the relevant workspace files and constraints.",
                "Prepare the smallest complete implementation with focused tests.",
                "Run the fixed checks and report the result.",
                "Request approval before applying or publishing any risky change.",
            ]
        return [
            "Inspect the relevant workspace files and configuration.",
            "Collect evidence and explain the architecture or failure clearly.",
            "Run the requested checks and report the result.",
        ]
    if target_agent == "research":
        return [
            "Frame the question and identify the important claims to verify.",
            "Gather relevant evidence and distinguish facts from inference.",
            "Synthesize the answer with sources and practical next steps.",
        ]
    return [
        "Turn the request into a concrete operational outcome.",
        "Prepare the draft, schedule, checklist, or recommendation using sensible defaults.",
        "Request approval before sending, scheduling, posting, or changing an external system.",
    ]
