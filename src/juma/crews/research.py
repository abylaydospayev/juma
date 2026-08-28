from __future__ import annotations

from juma.models import ModelClient
from juma.state import JumaState

from .common import model_request, single_worker_graph


def build_research_crew(model: ModelClient):
    def research_worker(state: JumaState) -> dict:
        return {
            "response": model.generate("research", model_request(state)),
            "proposed_action": None,
            "events": [{"source": "research", "message": "Research crew generated a response."}],
        }

    return single_worker_graph("research_worker", research_worker)
