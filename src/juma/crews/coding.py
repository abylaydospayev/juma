from __future__ import annotations

import re

from juma.actions import coding_action, patch_action
from juma.models import ModelClient, PatchGenerationError
from juma.patches import PatchManager
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
        response = model.generate("coding", model_request(state), proposed_action=initial_action)
        patch = PatchManager.extract(response)
        if not patch and _is_change_request(state["request"]):
            raise PatchGenerationError(
                "The coding crew did not return a unified patch for the requested change. "
                "No files were modified."
            )
        action = (
            patch_action(state["request"], patch, patch_manager.files(patch))
            if patch
            else initial_action
        )
        return {
            "response": response,
            "proposed_action": action,
            "events": [{"source": "coding", "message": "Coding crew generated a response."}],
        }

    return single_worker_graph("coding_worker", coding_worker)


def _is_change_request(request: str) -> bool:
    lowered = request.casefold()
    words = set(re.findall(r"[a-z]+", request.casefold()))
    if "update me" in lowered or "give me an update" in lowered:
        return False
    return bool(words & set(CHANGE_TERMS))
