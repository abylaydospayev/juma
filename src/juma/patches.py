from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from .workspace import IGNORED_PARTS, WorkspaceTools


class PatchError(ValueError):
    """Raised when a generated patch is unsafe or cannot be applied."""


class PatchManager:
    """Validate, apply, test, and reverse approved Git unified diffs."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.workspace = WorkspaceTools(self.root)

    @classmethod
    def cwd(cls) -> PatchManager:
        return cls(Path.cwd())

    @staticmethod
    def extract(response: str) -> str | None:
        tagged = re.search(
            r"<juma-patch>\s*(.*?)\s*</juma-patch>",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if tagged:
            candidate = tagged.group(1).strip()
        else:
            fenced = re.search(
                r"```(?:diff|patch)?\s*\n(.*?)```",
                response,
                re.DOTALL | re.IGNORECASE,
            )
            candidate = fenced.group(1).strip() if fenced else ""
        if not PatchManager._looks_like_diff(candidate):
            lines = response.splitlines()
            starts = [
                index
                for index, line in enumerate(lines)
                if line.startswith("diff --git ") or line.startswith("--- a/")
            ]
            if not starts:
                return None
            candidate = "\n".join(lines[starts[0] :]).strip()
        if not PatchManager._looks_like_diff(candidate):
            return None
        return candidate.replace("\r\n", "\n") + "\n"

    @staticmethod
    def _looks_like_diff(candidate: str) -> bool:
        lines = candidate.splitlines()
        return any(line.startswith("--- ") for line in lines) and any(
            line.startswith("+++ ") for line in lines
        )

    def files(self, patch: str) -> list[str]:
        paths: list[str] = []
        for line in patch.splitlines():
            if not (line.startswith("--- ") or line.startswith("+++ ")):
                continue
            raw = line[4:].split("\t", 1)[0].split(" ", 1)[0]
            if raw == "/dev/null":
                continue
            if raw.startswith(("a/", "b/")):
                raw = raw[2:]
            self._validate_path(raw)
            if raw not in paths:
                paths.append(raw)
        if not paths:
            raise PatchError("The patch does not name a workspace file.")
        return paths

    def validate(self, patch: str) -> list[str]:
        files = self.files(patch)
        completed = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", "-"],
            cwd=self.root,
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            raise PatchError(f"Git rejected the patch: {detail}")
        return files

    def apply_and_test(self, patch: str) -> dict[str, Any]:
        try:
            files = self.validate(patch)
        except (OSError, subprocess.SubprocessError, PatchError) as exc:
            return {"status": "apply_failed", "files": [], "error": str(exc)}
        try:
            applied = subprocess.run(
                ["git", "apply", "--whitespace=nowarn", "-"],
                cwd=self.root,
                input=patch,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "apply_failed", "files": files, "error": str(exc)}
        if applied.returncode:
            detail = (applied.stderr or applied.stdout).strip()
            return {"status": "apply_failed", "files": files, "error": detail}
        test = self._run_tests()
        status = "applied_tests_passed" if test["return_code"] == 0 else "applied_tests_failed"
        return {"status": status, "files": files, "test": test}

    def rollback(self, patch: str) -> dict[str, Any]:
        try:
            files = self.files(patch)
            reversed_patch = subprocess.run(
                ["git", "apply", "--reverse", "--whitespace=nowarn", "-"],
                cwd=self.root,
                input=patch,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError, PatchError) as exc:
            return {"status": "apply_failed", "files": [], "error": str(exc)}
        if reversed_patch.returncode:
            detail = (reversed_patch.stderr or reversed_patch.stdout).strip()
            return {"status": "apply_failed", "files": files, "error": detail}
        test = self._run_tests()
        return {"status": "rolled_back", "files": files, "test": test}

    def _run_tests(self) -> dict[str, Any]:
        try:
            return self.workspace.run_checks("tests")
        except (OSError, subprocess.SubprocessError) as exc:
            return {"check": "tests", "return_code": -1, "output": str(exc)}

    def _validate_path(self, relative_path: str) -> None:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PatchError("The patch contains a path outside the workspace.") from exc
        if any(part in IGNORED_PARTS for part in Path(relative_path).parts):
            raise PatchError("The patch targets an excluded workspace directory.")
