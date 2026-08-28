from __future__ import annotations

from juma.actions import admin_action
from juma.models import ModelClient
from juma.state import JumaState

from .common import model_request, single_worker_graph


def build_admin_crew(model: ModelClient):
    def admin_worker(state: JumaState) -> dict:
        action = admin_action(state["request"])
        response = model.generate("admin", model_request(state), proposed_action=action)
        return {
            "response": response,
            "proposed_action": action,
            "events": [{"source": "admin", "message": "Admin crew generated a response."}],
        }

    return single_worker_graph("admin_worker", admin_worker)
