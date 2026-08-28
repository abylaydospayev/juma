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
    patch: str | None = None,
) -> ProposedAction:
    payload = {
        "kind": kind,
        "summary": summary,
        "risk": risk,
        "parameters": parameters or {},
    }
    if patch is not None:
        payload["patch"] = patch
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**payload, "fingerprint": fingerprint}  # type: ignore[return-value]


def patch_action(request: str, patch: str, files: list[str]) -> ProposedAction:
    return make_action(
        "code.patch",
        f"Apply the proposed patch for: {request}",
        "medium",
        parameters={"files": files},
        patch=patch,
    )


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
