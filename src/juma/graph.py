from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .actions import patch_action
from .config import Settings
from .crews import build_admin_crew, build_coding_crew, build_research_crew
from .memory import MemoryStore
from .models import JumaModelError, ModelClient
from .patches import PatchError, PatchManager
from .planner import build_plan
from .router import route_node, selected_crew
from .safety import approval_gate
from .state import JumaState


def build_graph(
    checkpointer,
    model: ModelClient,
    memory: MemoryStore | None = None,
    patch_manager: PatchManager | None = None,
    settings: Settings | None = None,
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
    builder.add_node(
        "planner",
        lambda state: {
            "plan": build_plan(state["request"], state["target_agent"]),
            "events": [
                {
                    "source": "juma",
                    "message": "Juma formed a bounded execution plan.",
                }
            ],
        },
    )
    builder.add_node("coding", lambda state: invoke_crew("coding", state))
    builder.add_node("research", lambda state: invoke_crew("research", state))
    builder.add_node("admin", lambda state: invoke_crew("admin", state))
    builder.add_node("approval", approval_gate)

    def execute_patch(state: JumaState) -> dict:
        action = state.get("proposed_action")
        if not action or action.get("kind") != "code.patch" or not action.get("patch"):
            return {}
        events = []
        branch: str | None = None
        autonomous = bool(settings and (settings.auto_repair or settings.auto_commit))
        if autonomous:
            branch_result = patch_manager.prepare_autonomous_branch(action.get("fingerprint", ""))
            if branch_result["status"] != "ready":
                result = {
                    "status": "apply_failed",
                    "files": [],
                    "error": branch_result["error"],
                }
            else:
                branch = branch_result["branch"]
                events.append(
                    {
                        "source": "automation",
                        "message": f"Prepared dedicated branch {branch}.",
                    }
                )
                try:
                    baseline = patch_manager.file_contents(
                        patch_manager.files(action["patch"])
                    )
                except PatchError as error:
                    result = {"status": "apply_failed", "files": [], "error": str(error)}
                else:
                    result = patch_manager.apply_and_test(action["patch"])
        else:
            result = patch_manager.apply_and_test(action["patch"])

        final_action = action
        if branch:
            result["branch"] = branch

        if (
            result["status"] == "applied_tests_failed"
            and settings
            and settings.auto_repair
            and settings.max_repair_attempts > 0
        ):
            result, final_action, repair_events = _repair_until_passing(
                state,
                action,
                result,
                model,
                patch_manager,
                baseline,
                settings.max_repair_attempts,
            )
            events.extend(repair_events)

        if (
            result["status"] == "applied_tests_passed"
            and settings
            and settings.auto_commit
        ):
            commit = patch_manager.commit(
                result["files"],
                "juma: apply approved coding change",
                expected_branch=branch,
            )
            result["commit"] = commit
            if commit["status"] == "committed":
                events.append(
                    {
                        "source": "commit",
                        "message": (
                            f"Created commit {commit['revision'][:12]} on {commit['branch']}."
                        ),
                    }
                )
            else:
                events.append(
                    {
                        "source": "commit",
                        "message": f"Automatic commit failed: {commit['error']}",
                    }
                )

        if result["status"] == "applied_tests_passed":
            response = state["response"] + " The approved patch was applied and all tests passed."
            if final_action is not action:
                response += " Juma repaired the failed test run automatically."
            if result.get("commit", {}).get("status") == "committed":
                response += f" Commit created on {result['commit']['branch']}."
            elif result.get("commit", {}).get("status") == "commit_failed":
                response += f" Automatic commit failed: {result['commit']['error']}"
            status = "completed"
        elif result["status"] == "applied_tests_failed":
            response = (
                state["response"]
                + " The approved patch was applied, but the post-change tests failed. "
                "Automatic repair did not reach a passing test run. You can roll it back "
                "from the UI."
            )
            status = "completed"
        else:
            response = state["response"] + f" The patch could not be applied: {result['error']}"
            status = "failed"
        events.append(
            {
                "source": "patch",
                "message": f"Patch result: {result['status']}.",
            }
        )
        return {
            "response": response,
            "proposed_action": final_action,
            "patch_result": result,
            "rollback_available": result["status"] == "applied_tests_failed",
            "status": status,
            "events": events,
        }

    def after_approval(state: JumaState) -> str:
        approval = state.get("approval")
        action = state.get("proposed_action")
        if approval and approval["approved"] and action and action.get("kind") == "code.patch":
            return "execute_patch"
        return "done"

    builder.add_node("execute_patch", execute_patch)

    builder.add_edge(START, "router")
    builder.add_edge("router", "planner")
    builder.add_conditional_edges(
        "planner",
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


def _repair_until_passing(
    state: JumaState,
    original_action: dict,
    initial_result: dict,
    model: ModelClient,
    patch_manager: PatchManager,
    baseline: dict[str, str | None],
    max_attempts: int,
) -> tuple[dict, dict, list[dict]]:
    """Repair a failed approved patch with bounded, validated incremental patches."""
    original_files = initial_result["files"]

    attempts: list[dict] = []
    events: list[dict] = []
    current_result = initial_result
    final_action = original_action
    test_output = (initial_result.get("test") or {}).get("output", "")
    for attempt_number in range(1, max_attempts + 1):
        repair_request = (
            f"{state['request']}\n\n"
            "The approved coding patch is applied in the current workspace, but its tests "
            "failed. Repair the implementation using only these already-approved files: "
            f"{', '.join(original_files)}. This is repair attempt {attempt_number} of "
            f"{max_attempts}. Inspect the current workspace and test output, then return a "
            "complete structured code change with focused tests as needed. Do not modify any "
            "other files.\n\nTest output:\n"
            f"{test_output}"
        )
        try:
            response = model.generate(
                "coding",
                repair_request,
                proposed_action=original_action,
            )
            repair_patch = PatchManager.extract(response)
            if not repair_patch:
                raise PatchError("The repair response contained no unified diff.")
            repair_files = patch_manager.validate(repair_patch)
            unexpected = sorted(set(repair_files) - set(original_files))
            if unexpected:
                raise PatchError(
                    "The repair patch targets files outside the approved scope: "
                    + ", ".join(unexpected)
                )
        except (JumaModelError, PatchError) as error:
            attempts.append(
                {"attempt": attempt_number, "status": "invalid_patch", "error": str(error)}
            )
            events.append(
                {
                    "source": "repair",
                    "message": f"Repair attempt {attempt_number} was not usable: {error}",
                }
            )
            continue

        repair_action = patch_action(state["request"], repair_patch, repair_files)
        repair_result = patch_manager.apply_and_test(repair_patch)
        attempt_record = {
            "attempt": attempt_number,
            "fingerprint": repair_action["fingerprint"],
            "files": repair_files,
            "status": repair_result["status"],
            "test": repair_result.get("test"),
        }
        if repair_result["status"] == "applied_tests_passed":
            try:
                combined_patch = patch_manager.from_file_snapshots(baseline)
                combined_files = patch_manager.files(combined_patch)
            except PatchError as error:
                attempt_record.update({"status": "combined_patch_failed", "error": str(error)})
                attempts.append(attempt_record)
                break
            attempts.append(attempt_record)
            current_result = {
                **repair_result,
                "files": combined_files,
                "pre_apply_hashes": {
                    relative_path: initial_result.get("pre_apply_hashes", {}).get(relative_path)
                    for relative_path in combined_files
                },
                "post_apply_hashes": patch_manager.file_hashes(combined_files),
                "repair_attempts": attempts,
            }
            final_action = patch_action(state["request"], combined_patch, combined_files)
            events.append(
                {
                    "source": "repair",
                    "message": f"Automatic repair passed tests on attempt {attempt_number}.",
                }
            )
            return current_result, final_action, events

        attempts.append(attempt_record)
        if repair_result["status"] == "applied_tests_failed":
            undone = patch_manager.rollback(
                repair_patch,
                expected_post_apply_hashes=repair_result.get("post_apply_hashes"),
                expected_pre_apply_hashes=repair_result.get("pre_apply_hashes"),
            )
            attempts[-1]["rollback"] = {
                "status": undone["status"],
                "error": undone.get("error"),
            }
            if undone["status"] != "rolled_back":
                events.append(
                    {
                        "source": "repair",
                        "message": (
                            f"Repair attempt {attempt_number} could not be safely rolled back; "
                            "automatic repair stopped."
                        ),
                    }
                )
                break
        test_output = (repair_result.get("test") or {}).get("output", "")
        events.append(
            {
                "source": "repair",
                "message": f"Repair attempt {attempt_number} did not pass tests.",
            }
        )

    current_result["repair_attempts"] = attempts
    return current_result, final_action, events
