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

BROKEN_PATCH = """diff --git a/target.py b/target.py
--- a/target.py
+++ b/target.py
@@ -1,2 +1,2 @@
 def value():
-    return 1
+    return 3
"""

REPAIR_PATCH = """diff --git a/target.py b/target.py
--- a/target.py
+++ b/target.py
@@ -1,2 +1,2 @@
 def value():
-    return 3
+    return 2
"""

STILL_BROKEN_PATCH = """diff --git a/target.py b/target.py
--- a/target.py
+++ b/target.py
@@ -1,2 +1,2 @@
 def value():
-    return 3
+    return 4
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


def repair_repository(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    (tmp_path / "target.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "test_smoke.py").write_text(
        "from target import value\n\n\ndef test_value():\n    assert value() in {1, 2}\n",
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


class AutoRepairModel:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, crew, request, *, proposed_action=None) -> str:
        self.calls += 1
        patch = BROKEN_PATCH if self.calls == 1 else REPAIR_PATCH
        return "<juma-patch>\n" + patch + "</juma-patch>"


class ExhaustedAutoRepairModel(AutoRepairModel):
    def generate(self, crew, request, *, proposed_action=None) -> str:
        self.calls += 1
        patch = BROKEN_PATCH if self.calls == 1 else STILL_BROKEN_PATCH
        return "<juma-patch>\n" + patch + "</juma-patch>"


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
    assert result["pre_apply_hashes"]["target.py"]
    assert result["post_apply_hashes"]["target.py"]
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 2\n"

    rolled_back = manager.rollback(
        PATCH,
        expected_post_apply_hashes=result["post_apply_hashes"],
        expected_pre_apply_hashes=result["pre_apply_hashes"],
    )
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["test"]["return_code"] == 0
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 1\n"


def test_rollback_refuses_post_apply_file_tampering(tmp_path: Path) -> None:
    repository(tmp_path)
    manager = PatchManager(tmp_path)
    result = manager.apply_and_test(PATCH)
    (tmp_path / "target.py").write_text("value = attacker\n", encoding="utf-8")

    rolled_back = manager.rollback(
        PATCH,
        expected_post_apply_hashes=result["post_apply_hashes"],
        expected_pre_apply_hashes=result["pre_apply_hashes"],
    )

    assert rolled_back["status"] == "apply_failed"
    assert "changed after patch application" in rolled_back["error"]
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = attacker\n"


def test_patch_manager_builds_exact_git_diff_from_file_contents(tmp_path: Path) -> None:
    repository(tmp_path)
    manager = PatchManager(tmp_path)

    patch = manager.from_file_changes(
        [
            {"path": "new.py", "operation": "upsert", "content": "answer = 42"},
            {"path": "target.py", "operation": "upsert", "content": "value = 2\n"},
        ]
    )

    assert "*** Add File" not in patch
    assert "\\ No newline at end of file" in patch
    assert manager.validate(patch) == ["new.py", "target.py"]


def test_patch_extraction_preserves_trailing_blank_context(tmp_path: Path) -> None:
    repository(tmp_path)
    target = tmp_path / "target.py"
    target.write_text("value = 1\n\n", encoding="utf-8")
    manager = PatchManager(tmp_path)
    patch = manager.from_file_changes(
        [{"path": "target.py", "operation": "upsert", "content": "value = 2\n\n"}]
    )

    extracted = manager.extract(f"Summary\n<juma-patch>\n{patch}</juma-patch>")

    assert extracted == patch
    assert manager.validate(extracted) == ["target.py"]


def test_patch_requires_approval_and_exposes_rollback(tmp_path: Path) -> None:
    repository(tmp_path)
    with Juma(settings(tmp_path), model=PatchModel()) as juma:
        paused = juma.ask("fix the code in target.py", thread_id="patch-thread")
        assert paused["status"] == "waiting_approval"
        action = paused["interrupts"][0]["action"]
        assert action["kind"] == "code.patch"
        assert len(action["fingerprint"]) == 64
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
        assert finished["state"]["patch_result"]["post_apply_hashes"]["target.py"]
        assert finished["state"]["rollback_available"] is True

        with pytest.raises(ValueError, match="exact action fingerprint"):
            juma.rollback("patch-thread", action_fingerprint="wrong")

        rolled_back = juma.rollback(
            "patch-thread",
            action_fingerprint=fingerprint,
        )
        assert rolled_back["status"] == "completed"
        assert rolled_back["state"]["patch_result"]["status"] == "rolled_back"
        assert (tmp_path / "target.py").read_text(encoding="utf-8") == "value = 1\n"

        with pytest.raises(ValueError, match="No failed patch"):
            juma.rollback("patch-thread")


def test_autonomous_repair_commits_only_after_tests_pass(tmp_path: Path) -> None:
    repair_repository(tmp_path)
    data_dir = tmp_path.parent / f"{tmp_path.name}-runtime"
    auto_settings = Settings(
        data_dir,
        data_dir / "checkpoints.sqlite",
        data_dir / "memory.sqlite",
        workspace_root=tmp_path,
        auto_repair=True,
        max_repair_attempts=2,
        auto_commit=True,
    )
    model = AutoRepairModel()

    with Juma(auto_settings, model=model) as juma:
        paused = juma.ask("fix the code in target.py", thread_id="auto-thread")
        fingerprint = paused["interrupts"][0]["action"]["fingerprint"]
        finished = juma.resume(
            "auto-thread",
            approved=True,
            action_fingerprint=fingerprint,
        )

    assert model.calls == 2
    assert finished["status"] == "completed"
    assert finished["state"]["patch_result"]["status"] == "applied_tests_passed"
    assert finished["state"]["patch_result"]["repair_attempts"][0]["status"] == (
        "applied_tests_passed"
    )
    assert finished["state"]["patch_result"]["commit"]["status"] == "committed"
    assert finished["state"]["proposed_action"]["fingerprint"] != fingerprint
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == (
        "def value():\n    return 2\n"
    )
    assert subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip().startswith("juma/auto/")


def test_autonomous_repair_stops_at_limit_and_preserves_rollback(tmp_path: Path) -> None:
    repair_repository(tmp_path)
    data_dir = tmp_path.parent / f"{tmp_path.name}-runtime"
    auto_settings = Settings(
        data_dir,
        data_dir / "checkpoints.sqlite",
        data_dir / "memory.sqlite",
        workspace_root=tmp_path,
        auto_repair=True,
        max_repair_attempts=1,
        auto_commit=True,
    )
    model = ExhaustedAutoRepairModel()

    with Juma(auto_settings, model=model) as juma:
        paused = juma.ask("fix the code in target.py", thread_id="limited-auto-thread")
        fingerprint = paused["interrupts"][0]["action"]["fingerprint"]
        finished = juma.resume(
            "limited-auto-thread",
            approved=True,
            action_fingerprint=fingerprint,
        )

        assert model.calls == 2
        assert finished["state"]["patch_result"]["status"] == "applied_tests_failed"
        assert len(finished["state"]["patch_result"]["repair_attempts"]) == 1
        assert "commit" not in finished["state"]["patch_result"]
        rolled_back = juma.rollback(
            "limited-auto-thread",
            action_fingerprint=fingerprint,
        )

    assert rolled_back["status"] == "completed"
    assert (tmp_path / "target.py").read_text(encoding="utf-8") == (
        "def value():\n    return 1\n"
    )
