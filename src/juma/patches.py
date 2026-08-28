from __future__ import annotations

import difflib
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
            r"<juma-patch>(.*?)</juma-patch>",
            response,
            re.DOTALL | re.IGNORECASE,
        )
        if tagged:
            candidate = PatchManager._remove_wrapper_newlines(tagged.group(1))
        else:
            fenced = re.search(
                r"```(?:diff|patch)?\s*\n(.*?)```",
                response,
                re.DOTALL | re.IGNORECASE,
            )
            candidate = PatchManager._remove_wrapper_newlines(fenced.group(1)) if fenced else ""
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
        candidate = candidate.replace("\r\n", "\n")
        return candidate if candidate.endswith("\n") else candidate + "\n"

    @staticmethod
    def _remove_wrapper_newlines(candidate: str) -> str:
        """Remove tag/fence separators without trimming meaningful diff whitespace."""
        if candidate.startswith("\r\n"):
            candidate = candidate[2:]
        elif candidate.startswith("\n"):
            candidate = candidate[1:]
        if candidate.endswith("\r\n"):
            candidate = candidate[:-2]
        elif candidate.endswith("\n"):
            candidate = candidate[:-1]
        return candidate

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
        non_git_markers = (
            "*** Begin Patch",
            "*** End Patch",
            "*** Add File:",
            "*** Update File:",
            "*** Delete File:",
        )
        if any(line.startswith(non_git_markers) for line in patch.splitlines()):
            raise PatchError(
                "The patch mixes Git unified diff content with unsupported apply-patch markers."
            )
        files = self.files(patch)
        try:
            completed = subprocess.run(
                [
                    "git",
                    "apply",
                    "--check",
                    "--ignore-whitespace",
                    "--whitespace=nowarn",
                    "-",
                ],
                cwd=self.root,
                input=patch,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PatchError(f"Git could not validate the patch: {exc}") from exc
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
                ["git", "apply", "--ignore-whitespace", "--whitespace=nowarn", "-"],
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
                [
                    "git",
                    "apply",
                    "--ignore-whitespace",
                    "--reverse",
                    "--whitespace=nowarn",
                    "-",
                ],
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

    def from_file_changes(self, changes: list[dict[str, Any]]) -> str:
        """Build an exact Git diff from proposed final file contents."""
        sections: list[str] = []
        seen: set[str] = set()
        for change in changes:
            relative_path = str(change.get("path", ""))
            operation = str(change.get("operation", "upsert"))
            if relative_path in seen:
                raise PatchError(f"The patch lists a file more than once: {relative_path}")
            seen.add(relative_path)
            self._validate_path(relative_path)
            if operation not in {"upsert", "delete"}:
                raise PatchError(f"Unsupported file operation: {operation}")
            target = self.root / relative_path
            existed = target.is_file()
            try:
                old_content = target.read_text(encoding="utf-8") if existed else ""
            except (OSError, UnicodeDecodeError) as exc:
                raise PatchError(f"Cannot read {relative_path}: {exc}") from exc
            new_content = "" if operation == "delete" else str(change.get("content", ""))
            if old_content == new_content:
                continue
            from_path = f"a/{relative_path}" if existed else "/dev/null"
            to_path = f"b/{relative_path}" if operation == "upsert" else "/dev/null"
            diff = difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=from_path,
                tofile=to_path,
                lineterm="\n",
            )
            header = f"diff --git a/{relative_path} b/{relative_path}\n"
            if not existed:
                header += "new file mode 100644\n"
            elif operation == "delete":
                header += "deleted file mode 100644\n"
            sections.append(header + self._complete_diff_lines(diff))
        if not sections:
            raise PatchError("The proposed file changes do not modify the workspace.")
        return "".join(sections)

    @staticmethod
    def _complete_diff_lines(lines: Any) -> str:
        """Preserve missing final newlines without corrupting the next diff section."""
        completed: list[str] = []
        for line in lines:
            if line.endswith("\n"):
                completed.append(line)
                continue
            completed.append(line + "\n")
            if line.startswith(("+", "-", " ")):
                completed.append("\\ No newline at end of file\n")
        return "".join(completed)

    def _validate_path(self, relative_path: str) -> None:
        if not relative_path or relative_path in {".", ".."}:
            raise PatchError("The patch contains an empty or invalid workspace path.")
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise PatchError("The patch contains a path outside the workspace.") from exc
        if any(part in IGNORED_PARTS for part in Path(relative_path).parts):
            raise PatchError("The patch targets an excluded workspace directory.")
