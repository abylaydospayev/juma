from __future__ import annotations

from .state import AgentName, JumaState


def _deterministic_decision(request: str) -> dict[str, str | float | AgentName]:
    words = set(request.lower().replace("/", " ").replace("-", " ").split())
    scores = {
        "coding": len(words & CODING_TERMS),
        "research": len(words & RESEARCH_TERMS),
        "admin": len(words & ADMIN_TERMS),
    }
    target = classify(request)
    ordered = sorted(scores.values(), reverse=True)
    top = ordered[0]
    second = ordered[1]
    if top == 0:
        confidence = 0.35
        reason = "No domain keywords matched; using the research default."
    elif top == second:
        confidence = 0.55
        reason = f"Several crews matched equally; selected {target} by default tie-break."
    else:
        confidence = min(0.98, 0.65 + 0.1 * (top - second) + 0.05 * top)
        reason = f"Matched {top} {target} signal(s), ahead of the next crew by {top - second}."
    return {"target_agent": target, "confidence": confidence, "reason": reason}


CODING_TERMS = {
    "add",
    "api",
    "code",
    "bug",
    "build",
    "compile",
    "database",
    "delete",
    "deploy",
    "file",
    "git",
    "python",
    "remove",
    "repository",
    "script",
    "endpoint",
    "server",
    "sql",
    "test",
}
RESEARCH_TERMS = {
    "compare",
    "find",
    "latest",
    "paper",
    "pdf",
    "research",
    "search",
    "source",
    "study",
    "web",
}
ADMIN_TERMS = {
    "calendar",
    "email",
    "invite",
    "meeting",
    "message",
    "schedule",
    "slack",
    "send",
}


def classify(request: str) -> AgentName:
    """Provide the deterministic fallback used when structured routing is unavailable."""
    words = set(request.lower().replace("/", " ").replace("-", " ").split())
    scores = {
        "coding": len(words & CODING_TERMS),
        "research": len(words & RESEARCH_TERMS),
        "admin": len(words & ADMIN_TERMS),
    }
    return max(scores, key=lambda name: (scores[name], name == "research"))  # type: ignore[return-value]


def route_request(request: str) -> dict[str, str | float | AgentName]:
    """Return an inspectable routing decision and confidence estimate."""
    return _deterministic_decision(request)


def route_node(state: JumaState, *, model: object | None = None) -> dict:
    decision = (
        model.route(state["request"])  # type: ignore[attr-defined]
        if model is not None and callable(getattr(model, "route", None))
        else route_request(state["request"])
    )
    target = decision["target_agent"]
    return {
        "target_agent": target,
        "route_confidence": decision["confidence"],
        "route_reason": decision["reason"],
        "status": "working",
        "events": [
            {
                "source": "router",
                "message": f"Routed to {target} ({decision['confidence']:.0%} confidence).",
            }
        ],
    }


def selected_crew(state: JumaState) -> AgentName:
    return state["target_agent"]
