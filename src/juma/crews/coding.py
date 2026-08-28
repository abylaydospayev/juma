from __future__ import annotations

from juma.actions import coding_action
from juma.models import ModelClient
from juma.state import JumaState

from .common import model_request, single_worker_graph


def build_coding_crew(model: ModelClient):
    def coding_worker(state: JumaState) -> dict:
        action = coding_action(state["request"])
        response = model.generate("coding", model_request(state), proposed_action=action)
        return {
            "response": response,
            "proposed_action": action,
            "events": [{"source": "coding", "message": "Coding crew generated a response."}],
        }

    return single_worker_graph("coding_worker", coding_worker)
