from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .crews import build_admin_crew, build_coding_crew, build_research_crew
from .memory import MemoryStore
from .models import ModelClient
from .patches import PatchManager
from .router import route_node, selected_crew
from .safety import approval_gate
from .state import JumaState


def build_graph(
    checkpointer,
    model: ModelClient,
    memory: MemoryStore | None = None,
    patch_manager: PatchManager | None = None,
):
    patch_manager = patch_manager or PatchManager(Path.cwd())
    crews = {
        "coding": build_coding_crew(model, patch_manager),
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

    def execute_patch(state: JumaState) -> dict:
        action = state.get("proposed_action")
        if not action or action.get("kind") != "code.patch" or not action.get("patch"):
            return {}
        result = patch_manager.apply_and_test(action["patch"])
        if result["status"] == "applied_tests_passed":
            response = state["response"] + " The approved patch was applied and all tests passed."
            status = "completed"
        elif result["status"] == "applied_tests_failed":
            response = (
                state["response"]
                + " The approved patch was applied, but the post-change tests failed. "
                "You can roll it back from the UI."
            )
            status = "completed"
        else:
            response = state["response"] + f" The patch could not be applied: {result['error']}"
            status = "failed"
        return {
            "response": response,
            "patch_result": result,
            "rollback_available": result["status"] == "applied_tests_failed",
            "status": status,
            "events": [
                {
                    "source": "patch",
                    "message": f"Patch result: {result['status']}.",
                }
            ],
        }

    def after_approval(state: JumaState) -> str:
        approval = state.get("approval")
        action = state.get("proposed_action")
        if approval and approval["approved"] and action and action.get("kind") == "code.patch":
            return "execute_patch"
        return "done"

    builder.add_node("execute_patch", execute_patch)

    builder.add_edge(START, "router")
    builder.add_conditional_edges(
        "router",
        selected_crew,
        {"coding": "coding", "research": "research", "admin": "admin"},
    )
    for crew in ("coding", "research", "admin"):
        builder.add_edge(crew, "approval")
    builder.add_conditional_edges(
        "approval",
        after_approval,
        {"execute_patch": "execute_patch", "done": END},
    )
    builder.add_edge("execute_patch", END)
    return builder.compile(checkpointer=checkpointer)
