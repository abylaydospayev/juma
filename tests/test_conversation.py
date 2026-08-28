from pathlib import Path

from juma.config import Settings
from juma.service import Juma


def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, tmp_path / "checkpoints.sqlite", tmp_path / "memory.sqlite")


class FakeModel:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def generate(self, crew, request, *, proposed_action=None) -> str:
        self.requests.append(request)
        return f"{crew}: {request}"


def test_conversation_history_survives_runtime_restart(tmp_path: Path) -> None:
    first_model = FakeModel()
    with Juma(settings(tmp_path), model=first_model) as juma:
        juma.ask("research durable memory", thread_id="conversation")

    second_model = FakeModel()
    with Juma(settings(tmp_path), model=second_model) as juma:
        result = juma.ask("what did we discuss?", thread_id="conversation")
        history = juma.history("conversation")

    assert result["status"] == "completed"
    assert len(history) == 4
    assert "Relevant conversation context" in second_model.requests[0]
    assert "research durable memory" in second_model.requests[0]


def test_threads_are_listed_by_recent_activity(tmp_path: Path) -> None:
    with Juma(settings(tmp_path), model=FakeModel()) as juma:
        juma.ask("research first chat", thread_id="first")
        juma.ask("research second chat", thread_id="second")
        threads = juma.threads()

    assert [thread["thread_id"] for thread in threads[:2]] == ["second", "first"]
    assert threads[0]["title"] == "research second chat"
