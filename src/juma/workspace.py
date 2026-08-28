from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "data"}
MAX_READ_BYTES = 200_000


WORKSPACE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "list_files",
        "description": "List files in the workspace or a safe relative directory.",
        "parameters": {
            "type": "object",
            "properties": {"directory": {"type": "string"}},
            "required": ["directory"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace. Use for inspection only.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "search_files",
        "description": "Search workspace text files for a plain-text query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "directory": {"type": "string"},
            },
            "required": ["query", "directory"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "run_checks",
        "description": "Run a fixed read-only project check: tests, lint, or compile.",
        "parameters": {
            "type": "object",
            "properties": {"check": {"type": "string", "enum": ["tests", "lint", "compile"]}},
            "required": ["check"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class WorkspaceTools:
    """Read-only workspace tools with path traversal and command restrictions."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self.root / (relative_path or ".")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Path is outside the configured workspace.") from exc
        if any(part in IGNORED_PARTS for part in candidate.relative_to(self.root).parts):
            raise ValueError("That workspace directory is excluded from inspection.")
        return candidate

    def list_files(self, directory: str = "") -> dict[str, Any]:
        path = self._safe_path(directory)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {directory}")
        files = []
        for child in sorted(path.iterdir()):
            if child.name in IGNORED_PARTS:
                continue
            files.append(str(child.relative_to(self.root)))
            if len(files) >= 200:
                break
        return {"directory": directory or ".", "files": files}

    def read_file(self, path: str) -> dict[str, Any]:
        target = self._safe_path(path)
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        if target.stat().st_size > MAX_READ_BYTES:
            raise ValueError(f"File is larger than the {MAX_READ_BYTES}-byte read limit.")
        return {"path": path, "content": target.read_text(encoding="utf-8")}

    def search_files(self, query: str, directory: str = "") -> dict[str, Any]:
        if not query:
            raise ValueError("Search query cannot be empty.")
        base = self._safe_path(directory)
        if not base.is_dir():
            raise ValueError(f"Not a directory: {directory}")
        matches: list[dict[str, Any]] = []
        for path in base.rglob("*"):
            if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line in enumerate(text.splitlines(), 1):
                if query.casefold() in line.casefold():
                    matches.append(
                        {
                            "path": str(path.relative_to(self.root)),
                            "line": line_number,
                            "text": line[:500],
                        }
                    )
                    if len(matches) >= 100:
                        return {"query": query, "matches": matches}
        return {"query": query, "matches": matches}

    def run_checks(self, check: str) -> dict[str, Any]:
        commands = {
            "tests": [sys.executable, "-m", "pytest", "-q"],
            "lint": [sys.executable, "-m", "ruff", "check", "."],
            "compile": [sys.executable, "-m", "compileall", "-q", "src"],
        }
        if check not in commands:
            raise ValueError(f"Unsupported check: {check}")
        completed = subprocess.run(
            commands[check],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        output = (completed.stdout + completed.stderr)[-12_000:]
        return {"check": check, "return_code": completed.returncode, "output": output}

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handlers = {
            "list_files": self.list_files,
            "read_file": self.read_file,
            "search_files": self.search_files,
            "run_checks": self.run_checks,
        }
        if name not in handlers:
            raise ValueError(f"Unknown workspace tool: {name}")
        try:
            return handlers[name](**arguments)
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            return {"error": str(exc)}
