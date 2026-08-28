from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .state import ProposedAction


def make_action(
    kind: str,
    summary: str,
    risk: str,
    *,
    parameters: dict[str, Any] | None = None,
) -> ProposedAction:
    payload = {
        "kind": kind,
        "summary": summary,
        "risk": risk,
        "parameters": parameters or {},
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return {**payload, "fingerprint": fingerprint}  # type: ignore[return-value]


def coding_action(request: str) -> ProposedAction | None:
    lowered = request.lower()
    if re.search(r"\b(delete|remove|erase|destroy|rm|drop)\b", lowered):
        return make_action(
            "filesystem.delete",
            request,
            "high",
            parameters={"request": request},
        )
    if re.search(r"\b(git\s+push|push\s+to\s+main|commit\s+to\s+main|deploy)\b", lowered):
        return make_action("code.publish", request, "high", parameters={"request": request})
    if re.search(r"\b(edit|modify|update|write|save|create)\b.*\b(file|code|script)\b", lowered):
        return make_action("filesystem.write", request, "medium", parameters={"request": request})
    return None


def admin_action(request: str) -> ProposedAction | None:
    lowered = request.lower()
    if re.search(r"\b(send|schedule|invite|post)\b", lowered):
        return make_action(
            "external.communication",
            request,
            "high",
            parameters={"request": request},
        )
    return None
