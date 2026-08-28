from pathlib import Path

import pytest

from juma.config import Settings
from juma.service import Juma


def settings(tmp_path: Path) -> Settings:
    return Settings(tmp_path, tmp_path / "checkpoints.sqlite", tmp_path / "memory.sqlite")


class FakeModel:
    def generate(self, crew, request, *, proposed_action=None) -> str:
        return f"{crew} answer: {request}"


class FailingModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, crew, request, *, proposed_action=None) -> str:
        self.calls += 1
        raise RuntimeError("model failed")


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


def test_failed_thread_cannot_be_approved_or_rerun(tmp_path: Path) -> None:
    model = FailingModel()
    with Juma(settings(tmp_path), model=model) as juma:
        with pytest.raises(RuntimeError, match="model failed"):
            juma.ask("research durable agent systems", thread_id="failed")

        with pytest.raises(ValueError, match="not waiting for approval"):
            juma.resume("failed", approved=True, action_fingerprint="placeholder")

    assert model.calls == 1
