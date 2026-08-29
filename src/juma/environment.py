"""Controlled project-environment discovery and setup for workspace checks."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


class EnvironmentSetupError(RuntimeError):
    """Raised when an isolated project environment cannot be prepared."""


class ProjectEnvironment:
    """Find or create a project interpreter without writing into the source tree."""

    def __init__(self, root: Path, *, auto_setup: bool = False, timeout: float = 600.0):
        self.root = root.resolve()
        self.auto_setup = auto_setup
        self.timeout = timeout

    def prepare(self) -> dict[str, Any]:
        """Return the interpreter Juma should use for checks and optionally provision it."""
        existing = self._existing_interpreter()
        if existing is not None:
            return {
                "status": "ready",
                "python": str(existing),
                "source": "project-venv",
                "created": False,
            }

        if not self.auto_setup or not self._has_install_manifest():
            return {
                "status": "ready",
                "python": sys.executable,
                "source": "juma-runtime",
                "created": False,
            }

        environment_dir = self._temporary_environment_dir()
        python = self._interpreter(environment_dir)
        if python is None:
            try:
                self._run([sys.executable, "-m", "venv", str(environment_dir)])
            except (OSError, subprocess.SubprocessError, EnvironmentSetupError) as exc:
                return {"status": "failed", "error": f"Could not create project environment: {exc}"}
            python = self._interpreter(environment_dir)
        if python is None:
            return {
                "status": "failed",
                "error": "The project environment was created without a usable Python interpreter.",
            }

        install_command = self._install_command(python)
        if install_command is not None:
            try:
                self._run(install_command)
            except (OSError, subprocess.SubprocessError, EnvironmentSetupError) as exc:
                return {"status": "failed", "python": str(python), "error": str(exc)}
        return {
            "status": "ready",
            "python": str(python),
            "source": "temporary-venv",
            "created": True,
        }

    def _existing_interpreter(self) -> Path | None:
        for directory in (self.root / ".venv", self.root / "venv"):
            interpreter = self._interpreter(directory)
            if interpreter is not None:
                return interpreter
        return None

    @staticmethod
    def _interpreter(directory: Path) -> Path | None:
        candidates = (
            directory / "Scripts" / "python.exe",
            directory / "bin" / "python",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    def _has_install_manifest(self) -> bool:
        return any(
            (self.root / name).is_file()
            for name in ("pyproject.toml", "setup.py", "requirements.txt")
        )

    def _temporary_environment_dir(self) -> Path:
        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:24]
        return Path(tempfile.gettempdir()) / "juma-environments" / digest

    def _install_command(self, python: Path) -> list[str] | None:
        pyproject = self.root / "pyproject.toml"
        if pyproject.is_file():
            try:
                import tomllib

                document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError):
                document = {}
            extras = document.get("project", {}).get("optional-dependencies", {})
            target = ".[dev]" if isinstance(extras, dict) and "dev" in extras else "."
            return [str(python), "-m", "pip", "install", "-e", target]
        requirements = self.root / "requirements.txt"
        if requirements.is_file():
            return [str(python), "-m", "pip", "install", "-r", str(requirements)]
        return None

    def _run(self, command: list[str]) -> None:
        completed = subprocess.run(
            command,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()
            detail = re.sub(r"(https?://)([^\s/@]+@)", r"\1<credentials>@", detail)
            raise EnvironmentSetupError(detail or f"Command exited with {completed.returncode}.")
