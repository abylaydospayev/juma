import subprocess
from pathlib import Path

import pytest

from juma.config import Settings
from juma.patches import PatchManager
from juma.service import Juma

PATCH = """diff --git a/target.py b/target.py
--- a/target.py
+++ b/target.py
@@ -1 +1 @@
-value = 1
+value = 2
"""


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def repository(tmp_path: Path) -> None:
    (tmp_path / "target.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text(
        "from target import value\n\n\ndef test_value():\n    assert value == 1\n",
        encoding="utf-8",
    )
    git("init", cwd=tmp_path)
    git("config", "user.email", "juma@example.invalid", cwd=tmp_path)
    git("config", "user.name", "juma tests", cwd=tmp_path)
    git("add", ".", cwd=tmp_path)
    git("commit", "-m", "baseline", cwd=tmp_path)


def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "runtime"
    return Settings(
        data_dir,
        data_dir / "checkpoints.sqlite",
        data_dir / "memory.sqlite",
        workspace_root=tmp_path,
    )


class PatchModel:
    def generate(self, crew, request, *, proposed_action=None) -> str:
        return "Here is the proposed change.\n<juma-patch>\n" + PATCH + "</juma-patch>"


class RetryPatchModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, crew, request, *, proposed_action=None) -> str:
        self.calls += 1
        if self.calls == 1:
            return "I need more context before changing files."
        return "<juma-patch>\n" + PATCH + "</juma-patch>"


class MalformedThenValidPatchModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, crew, request, *, proposed_action=None) -> str:
        self.calls += 1
        if self.calls == 1:
            malformed = PATCH + "*** Add File: extra.py\n+value = 3\n"
            return "<juma-patch>\n" + malformed + "</juma-patch>"
        return "<juma-patch>\n" + PATCH + "</juma-patch>"


def test_coding_crew_repairs_a_missing_patch(tmp_path: Path) -> None:
    repository(tmp_path)
    model = RetryPatchModel()
    with Juma(settings(tmp_path), model=model) as juma:
        paused = juma.ask("fix the code in target.py", thread_id="retry-thread")

    assert paused["status"] == "waiting_approval"
    assert model.calls == 2


def test_coding_crew_repairs_a_malformed_patch_before_approval(tmp_path: Path) -> None:
    repository(tmp_path)
    model = MalformedThenValidPatchModel()
    with Juma(settings(tmp_path), model=model) as juma:
        paused = juma.ask("fix the code in target.py", thread_id="repair-thread")

    assert paused["status"] == "waiting_approval"
    assert paused["interrupts"][0]["action"]["parameters"]["files"] == ["target.py"]
    assert model.calls == 2


def test_patch_manager_applies_and_rolls_back(tmp_path: Path) -> None:
    repository(tmp_path)
    manager = PatchManager(tmp_path)

    result = manager.apply_and_test(PATCH)
    assert result["status"] == "applied_tests_failed"
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 2\n"

    rolled_back = manager.rollback(PATCH)
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["test"]["return_code"] == 0
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 1\n"


def test_patch_manager_builds_exact_git_diff_from_file_contents(tmp_path: Path) -> None:
    repository(tmp_path)
    manager = PatchManager(tmp_path)

    patch = manager.from_file_changes(
        [
            {"path": "target.py", "operation": "upsert", "content": "value = 2\n"},
            {"path": "new.py", "operation": "upsert", "content": "answer = 42\n"},
        ]
    )

    assert "*** Add File" not in patch
    assert manager.validate(patch) == ["target.py", "new.py"]


def test_patch_requires_approval_and_exposes_rollback(tmp_path: Path) -> None:
    repository(tmp_path)
    with Juma(settings(tmp_path), model=PatchModel()) as juma:
        paused = juma.ask("fix the code in target.py", thread_id="patch-thread")
        assert paused["status"] == "waiting_approval"
        action = paused["interrupts"][0]["action"]
        assert action["kind"] == "code.patch"
        assert action["fingerprint"]
        assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 1\n"

        fingerprint = action["fingerprint"]
        with pytest.raises(ValueError, match="exact action fingerprint"):
            juma.resume("patch-thread", approved=True, action_fingerprint="wrong")

        finished = juma.resume(
            "patch-thread", approved=True, action_fingerprint=fingerprint
        )
        assert finished["status"] == "completed"
        assert finished["state"]["approval"]["action_fingerprint"] == fingerprint
        assert finished["state"]["patch_result"]["status"] == "applied_tests_failed"
        assert finished["state"]["rollback_available"] is True

        rolled_back = juma.rollback("patch-thread")
        assert rolled_back["status"] == "completed"
        assert rolled_back["state"]["patch_result"]["status"] == "rolled_back"
        assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 1\n"
