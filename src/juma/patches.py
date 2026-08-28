from __future__ import annotations

import difflib
import hashlib
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

    def prepare_autonomous_branch(self, fingerprint: str) -> dict[str, Any]:
        """Create a clean, dedicated branch for an autonomous coding run."""
        if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            return {"status": "branch_failed", "error": "Invalid patch fingerprint."}
        try:
            clean = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if clean.returncode:
                detail = (clean.stderr or clean.stdout).strip()
                return {"status": "branch_failed", "error": detail}
            if clean.stdout.strip():
                return {
                    "status": "branch_failed",
                    "error": "Autonomous runs require a clean Git working tree.",
                }

            current = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if current.returncode:
                detail = (current.stderr or current.stdout).strip()
                return {"status": "branch_failed", "error": detail}
            branch = f"juma/auto/{fingerprint}"
            current_branch = current.stdout.strip()
            if current_branch == branch:
                return {"status": "ready", "branch": branch}
            if current_branch.startswith("juma/auto/"):
                return {
                    "status": "branch_failed",
                    "error": f"Already on a different autonomous branch: {current_branch}.",
                }
            existing = subprocess.run(
                ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if existing.returncode == 0:
                return {
                    "status": "branch_failed",
                    "error": f"Autonomous branch already exists: {branch}.",
                }
            switched = subprocess.run(
                ["git", "switch", "--create", branch],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if switched.returncode:
                detail = (switched.stderr or switched.stdout).strip()
                return {"status": "branch_failed", "error": detail}
            return {"status": "ready", "branch": branch}
        except (OSError, subprocess.SubprocessError) as exc:
            return {"status": "branch_failed", "error": str(exc)}

    def file_contents(self, files: list[str]) -> dict[str, str | None]:
        """Capture UTF-8 contents for files before an autonomous change."""
        contents: dict[str, str | None] = {}
        for relative_path in files:
            self._validate_path(relative_path)
            target = self.root / relative_path
            if not target.is_file():
                contents[relative_path] = None
                continue
            try:
                contents[relative_path] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise PatchError(f"Cannot read {relative_path}: {exc}") from exc
        return contents

    def file_hashes(self, files: list[str]) -> dict[str, str | None]:
        """Return SHA-256 hashes for the selected workspace files."""
        for relative_path in files:
            self._validate_path(relative_path)
        return self._file_hashes(files)

    def commit(
        self,
        files: list[str],
        message: str,
        *,
        expected_branch: str | None = None,
    ) -> dict[str, Any]:
        """Commit only the approved run's files on its autonomous branch."""
        try:
            for relative_path in files:
                self._validate_path(relative_path)
            branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            current_branch = branch.stdout.strip()
            if branch.returncode or not current_branch.startswith("juma/auto/"):
                return {
                    "status": "commit_failed",
                    "error": "Automatic commits are allowed only on a juma/auto branch.",
                }
            if expected_branch is not None and current_branch != expected_branch:
                return {
                    "status": "commit_failed",
                    "error": f"The autonomous branch changed unexpectedly: {current_branch}.",
                }
            staged = subprocess.run(
                ["git", "add", "--", *files],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if staged.returncode:
                detail = (staged.stderr or staged.stdout).strip()
                return {"status": "commit_failed", "error": detail}
            check = subprocess.run(
                ["git", "diff", "--cached", "--check"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if check.returncode:
                detail = (check.stderr or check.stdout).strip()
                return {"status": "commit_failed", "error": detail}
            committed = subprocess.run(
                ["git", "commit", "-m", message.strip() or "juma: apply approved change"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if committed.returncode:
                detail = (committed.stderr or committed.stdout).strip()
                return {"status": "commit_failed", "error": detail}
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if revision.returncode:
                detail = (revision.stderr or revision.stdout).strip()
                return {"status": "commit_failed", "error": detail}
            return {
                "status": "committed",
                "branch": current_branch,
                "revision": revision.stdout.strip(),
            }
        except (OSError, subprocess.SubprocessError, PatchError) as exc:
            return {"status": "commit_failed", "error": str(exc)}

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
            pre_apply_hashes = self._file_hashes(files)
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
        post_apply_hashes = self._file_hashes(files)
        test = self._run_tests()
        status = "applied_tests_passed" if test["return_code"] == 0 else "applied_tests_failed"
        return {
            "status": status,
            "files": files,
            "pre_apply_hashes": pre_apply_hashes,
            "post_apply_hashes": post_apply_hashes,
            "test": test,
        }

    def rollback(
        self,
        patch: str,
        *,
        expected_post_apply_hashes: dict[str, str | None] | None = None,
        expected_pre_apply_hashes: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        try:
            files = self.files(patch)
            if expected_post_apply_hashes is not None:
                current_hashes = self._file_hashes(files)
                if current_hashes != expected_post_apply_hashes:
                    return {
                        "status": "apply_failed",
                        "files": files,
                        "error": (
                            "Workspace files changed after patch application; "
                            "rollback refused."
                        ),
                    }
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
        restored_hashes = self._file_hashes(files)
        if expected_pre_apply_hashes is not None and restored_hashes != expected_pre_apply_hashes:
            return {
                "status": "apply_failed",
                "files": files,
                "error": "Rollback did not restore the original file hashes.",
            }
        test = self._run_tests()
        return {
            "status": "rolled_back",
            "files": files,
            "restored_hashes": restored_hashes,
            "test": test,
        }

    def _file_hashes(self, files: list[str]) -> dict[str, str | None]:
        hashes: dict[str, str | None] = {}
        for relative_path in files:
            target = self.root / relative_path
            if not target.is_file():
                hashes[relative_path] = None
                continue
            digest = hashlib.sha256()
            with target.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[relative_path] = digest.hexdigest()
        return hashes

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

    def from_file_snapshots(self, snapshots: dict[str, str | None]) -> str:
        """Build a diff from captured baseline contents to current file contents."""
        sections: list[str] = []
        for relative_path, old_content in snapshots.items():
            self._validate_path(relative_path)
            target = self.root / relative_path
            existed = old_content is not None
            current_exists = target.is_file()
            try:
                new_content = target.read_text(encoding="utf-8") if current_exists else ""
            except (OSError, UnicodeDecodeError) as exc:
                raise PatchError(f"Cannot read {relative_path}: {exc}") from exc
            if existed == current_exists and old_content == new_content:
                continue
            from_path = f"a/{relative_path}" if existed else "/dev/null"
            to_path = f"b/{relative_path}" if current_exists else "/dev/null"
            diff = difflib.unified_diff(
                (old_content or "").splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=from_path,
                tofile=to_path,
                lineterm="\n",
            )
            header = f"diff --git a/{relative_path} b/{relative_path}\n"
            if not existed:
                header += "new file mode 100644\n"
            elif not current_exists:
                header += "deleted file mode 100644\n"
            sections.append(header + self._complete_diff_lines(diff))
        if not sections:
            raise PatchError("The current files match the captured baseline.")
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
