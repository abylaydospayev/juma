from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .crews import build_admin_crew, build_coding_crew, build_research_crew
from .memory import MemoryStore
from .models import ModelClient
from .router import route_node, selected_crew
from .safety import approval_gate
from .state import JumaState


def build_graph(checkpointer, model: ModelClient, memory: MemoryStore | None = None):
    crews = {
        "coding": build_coding_crew(model),
        "research": build_research_crew(model),
        "admin": build_admin_crew(model),
    }

    def invoke_crew(name: str, state: JumaState) -> dict:
        memory_context = (
            memory.search(state["request"], crew=name, limit=6) if memory is not None else []
        )
        # Reducer-backed fields must cross a subgraph boundary as deltas. Passing an
        # empty event list prevents the child from echoing the parent's history.
        result = crews[name].invoke({**state, "memory_context": memory_context, "events": []})
        return {
            "response": result["response"],
            "proposed_action": result.get("proposed_action"),
            "events": result.get("events", []),
        }

    builder = StateGraph(JumaState)
    builder.add_node("router", lambda state: route_node(state, model=model))
    builder.add_node("coding", lambda state: invoke_crew("coding", state))
    builder.add_node("research", lambda state: invoke_crew("research", state))
    builder.add_node("admin", lambda state: invoke_crew("admin", state))
    builder.add_node("approval", approval_gate)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        selected_crew,
        {"coding": "coding", "research": "research", "admin": "admin"},
    )
    for crew in ("coding", "research", "admin"):
        builder.add_edge(crew, "approval")
    builder.add_edge("approval", END)
    return builder.compile(checkpointer=checkpointer)
