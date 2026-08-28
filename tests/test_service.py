from pathlib import Path

from juma.config import Settings
from juma.service import Juma


def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, tmp_path / "checkpoints.sqlite", tmp_path / "memory.sqlite")


class FakeModel:
    def generate(self, crew, request, *, proposed_action=None) -> str:
        return f"{crew} answer: {request}"


def test_safe_request_completes(tmp_path: Path) -> None:
    with Juma(settings(tmp_path), model=FakeModel()) as juma:
        result = juma.ask("research durable agent systems", thread_id="safe")
    assert result["status"] == "completed"
    assert result["state"]["target_agent"] == "research"
    assert [event["source"] for event in result["state"]["events"]] == [
        "router",
        "research",
        "safety",
    ]


def test_risky_request_pauses_and_resumes(tmp_path: Path) -> None:
    with Juma(settings(tmp_path), model=FakeModel()) as juma:
        paused = juma.ask("delete file old.log", thread_id="risky")
        assert paused["status"] == "waiting_approval"
        assert paused["interrupts"][0]["action"]["kind"] == "filesystem.delete"

    # A new runtime proves the interrupt survives process/service restarts.
    with Juma(settings(tmp_path), model=FakeModel()) as juma:
        finished = juma.resume("risky", approved=False, feedback="Keep the logs")
    assert finished["status"] == "rejected"
    assert "Keep the logs" in finished["state"]["response"]
