from __future__ import annotations

import hmac
from typing import Any

from langgraph.types import interrupt

from .state import JumaState


def _fingerprints_match(expected: object, supplied: object) -> bool:
    return (
        isinstance(expected, str)
        and isinstance(supplied, str)
        and hmac.compare_digest(supplied, expected)
    )


def approval_gate(state: JumaState) -> dict[str, Any]:
    action = state.get("proposed_action")
    if not action:
        return {
            "status": "completed",
            "events": [{"source": "safety", "message": "No approval required."}],
        }

    decision = interrupt(
        {
            "type": "approval_required",
            "agent": state["target_agent"],
            "action": action,
            "action_fingerprint": action.get("fingerprint"),
            "request": state["request"],
        }
    )
    if isinstance(decision, bool):
        approved, feedback = decision, ""
    else:
        approved = bool(decision.get("approved", False))
        feedback = str(decision.get("feedback", ""))
    executable_kinds = {"code.patch", "filesystem.delete", "check.run"}
    # Rejections are intentionally not fingerprint-bound: a reviewer can reject a
    # stale or malformed proposal without first copying a hash from the preview.
    if approved and action.get("kind") in executable_kinds:
        supplied_fingerprint = (
            decision.get("action_fingerprint") if isinstance(decision, dict) else None
        )
        if not _fingerprints_match(action.get("fingerprint"), supplied_fingerprint):
            return {
                "approval": {
                    "approved": False,
                    "feedback": feedback,
                    "action_fingerprint": supplied_fingerprint,
                },
                "response": state["response"]
                + " The approval was rejected because it did not match the exact "
                "action fingerprint.",
                "status": "rejected",
                "events": [{"source": "safety", "message": "Patch fingerprint mismatch."}],
            }

    if approved:
        if action.get("kind") == "code.patch":
            suffix = " The patch was approved and will be applied with tests now."
        else:
            suffix = " The action was approved, but no production adapter is configured; no state was changed."
        return {
            "approval": {
                "approved": True,
                "feedback": feedback,
                "action_fingerprint": action.get("fingerprint"),
            },
            "response": state["response"] + suffix,
            "status": "succeeded" if action.get("kind") == "code.patch" else "failed",
            "events": [{"source": "safety", "message": "Action approved."}],
        }
    suffix = " The action was rejected."
    if feedback:
        suffix += f" Feedback: {feedback}"
    return {
        "approval": {
            "approved": False,
            "feedback": feedback,
            "action_fingerprint": action.get("fingerprint"),
        },
        "response": state["response"] + suffix,
        "status": "rejected",
        "events": [{"source": "safety", "message": "Action rejected."}],
    }
