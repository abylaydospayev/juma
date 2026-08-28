from __future__ import annotations

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from juma.state import JumaState


def single_worker_graph(name: str, worker: Callable[[JumaState], dict]):
    builder = StateGraph(JumaState)
    builder.add_node(name, worker)
    builder.add_edge(START, name)
    builder.add_edge(name, END)
    return builder.compile(checkpointer=False)


def contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in phrases)


def model_request(state: JumaState) -> str:
    """Add bounded conversation and memory context without changing the public API."""
    history = state.get("conversation_history", [])[-12:]
    memories = state.get("memory_context", [])[:6]
    plan = state.get("plan", [])
    preferences = state.get("user_preferences", {})
    if not history and not memories and not plan and not preferences:
        return state["request"]

    sections = [state["request"]]
    if plan:
        sections.append("\nJuma's execution plan:\n" + "\n".join(f"- {step}" for step in plan))
    if history:
        transcript = "\n".join(f"{item['role'].upper()}: {item['content']}" for item in history)
        sections.append(f"\nRelevant conversation context:\n{transcript}")
    if memories:
        recalled = "\n".join(f"- {item['content']}" for item in memories if item.get("content"))
        sections.append(f"\nRelevant shared memory:\n{recalled}")
    if preferences:
        configured = "\n".join(f"- {key}: {value}" for key, value in preferences.items())
        sections.append(f"\nUser preferences:\n{configured}")
    return "\n".join(sections)
