from __future__ import annotations

import re
import uuid

from juma.changeset import Action, Changeset, GuardrailReportModel
from juma.actions import coding_action, patch_action
from juma.models import ModelClient, PatchGenerationError
from juma.patches import PatchError, PatchManager
from juma.state import JumaState

from .common import model_request, single_worker_graph

CHANGE_TERMS = (
    "add",
    "change",
    "create",
    "edit",
    "fix",
    "implement",
    "modify",
    "refactor",
    "update",
)


def build_coding_crew(model: ModelClient, patch_manager: PatchManager | None = None):
    patch_manager = patch_manager or PatchManager.cwd()

    def coding_worker(state: JumaState) -> dict:
        initial_action = coding_action(state["request"])
        change_requested = _is_change_request(state["request"])
        response = model.generate("coding", model_request(state), proposed_action=initial_action)
        patch = PatchManager.extract(response)
        files, validation_error = _validate_patch(patch_manager, patch)
        correction_attempts = 0
        while change_requested and validation_error and correction_attempts < 2:
            correction_attempts += 1
            response = model.generate(
                "coding",
                model_request(state)
                + "\n\nThe previous coding patch was not usable. Validation error: "
                + validation_error
                + " Continue autonomously: choose the smallest conventional "
                "implementation that satisfies the request, create any missing component "
                "needed by the request, add focused tests, and return one complete Git unified "
                "diff inside <juma-patch> and </juma-patch>. Every file section must begin with "
                "diff --git. For new files, use new file mode, --- /dev/null, +++ b/path, and a "
                "valid hunk header. Never use *** Add File or *** Update File markers.",
                proposed_action=initial_action,
            )
            patch = PatchManager.extract(response)
            files, validation_error = _validate_patch(patch_manager, patch)
        if change_requested and validation_error:
            excerpt = " ".join(response.strip().split())[:600]
            raise PatchGenerationError(
                "The coding crew did not return a valid Git unified patch. "
                f"No files were modified. Validation error: {validation_error}. "
                f"Last model response: {excerpt or '[empty]'}"
            )
        action = (
            patch_action(
                state["request"],
                patch,
                files,
                expected_file_hashes=patch_manager.file_hashes(files),
                base_git_tree=patch_manager.base_git_tree(),
            )
            if patch
            else initial_action
        )
        changeset = None
        if patch and action:
            try:
                run_id = uuid.UUID(str(state.get("run_id")))
            except (ValueError, TypeError):
                run_id = uuid.uuid5(uuid.NAMESPACE_URL, str(state.get("run_id", "run")))
            try:
                thread_id = uuid.UUID(str(state.get("thread_id")))
            except (ValueError, TypeError):
                thread_id = uuid.uuid5(uuid.NAMESPACE_URL, str(state.get("thread_id", "thread")))
            report = patch_manager.last_guardrail_report
            expected_hashes = patch_manager.file_hashes(files)
            changeset = Changeset(
                run_id=run_id,
                thread_id=thread_id,
                workspace_id=str(state.get("workspace_id", "default")),
                intent=state["request"],
                actions=[
                    Action(
                        kind="code.patch",
                        summary=action["summary"],
                        risk=action["risk"],
                        payload={"patch": patch, "files": files},
                    )
                ],
                base_git_tree=patch_manager.base_git_tree(),
                expected_file_hashes={key: value or "" for key, value in expected_hashes.items()},
                guardrail_report=GuardrailReportModel.model_validate(report.as_dict()) if report else None,
            ).model_dump(mode="json")
            # The user-visible approval fingerprint is the immutable changeset
            # fingerprint. Keep the legacy action shape while binding it to the
            # richer canonical contract.
            action = {
                **action,
                "fingerprint": changeset["fingerprint"],
                "changeset_fingerprint": changeset["fingerprint"],
            }
        return {
            "response": response,
            "proposed_action": action,
            "changeset": changeset,
            "events": [{"source": "coding", "message": "Coding crew generated a response."}],
        }

    return single_worker_graph("coding_worker", coding_worker)


def _validate_patch(
    patch_manager: PatchManager, patch: str | None
) -> tuple[list[str], str | None]:
    if not patch:
        return [], "The response contained no unified diff."
    try:
        return patch_manager.validate(patch), None
    except PatchError as error:
        return [], str(error)


def _is_change_request(request: str) -> bool:
    lowered = request.casefold()
    words = set(re.findall(r"[a-z]+", request.casefold()))
    if "update me" in lowered or "give me an update" in lowered:
        return False
    return bool(words & set(CHANGE_TERMS))
