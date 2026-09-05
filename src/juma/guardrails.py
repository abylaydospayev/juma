"""Deterministic, non-executing proposal checks.

The guardrail layer intentionally runs before approval and never needs model or
workspace secrets.  It returns a structured report so callers can render useful
findings without persisting raw prompts, patches, or scanner evidence.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tomllib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:OPENAI|AWS|AZURE|GITHUB|SLACK|DATABASE)[A-Z0-9_]*\s*=\s*[^\s#]+"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
)
_ADDED_LINE = re.compile(r"^\+(?!\+\+\+)(.*)$")


@dataclass(slots=True)
class GuardrailFinding:
    code: str
    severity: str
    message: str
    path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            **({"path": self.path} if self.path else {}),
        }


@dataclass(slots=True)
class GuardrailReport:
    status: str = "pass"
    checks: list[str] = field(default_factory=list)
    findings: list[GuardrailFinding] = field(default_factory=list)

    def add(self, code: str, message: str, *, severity: str = "block", path: str | None = None) -> None:
        self.findings.append(GuardrailFinding(code, severity, message, path))
        if severity == "block":
            self.status = "block"
        elif self.status == "pass":
            self.status = "warn"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": list(self.checks),
            "findings": [finding.as_dict() for finding in self.findings],
        }


def scan_patch(patch: str, files: list[str] | None = None) -> GuardrailReport:
    """Scan a unified diff without applying or executing it."""
    report = GuardrailReport()
    report.checks.extend(["schema", "secret_scan", "patch_markers"])
    if not isinstance(patch, str) or not patch.strip():
        report.add("empty_patch", "The proposal did not contain a patch.")
        return report
    if len(patch.encode("utf-8")) > 2_000_000:
        report.add("resource_limit", "Patch exceeds the 2 MiB proposal limit.")
    if files is not None and len(files) > 100:
        report.add("resource_limit", "A proposal may target at most 100 files.")
    added_by_path: dict[str, list[str]] = {}
    current: str | None = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0].split(" ", 1)[0]
            current = raw[2:] if raw.startswith("b/") else raw
            added_by_path.setdefault(current, [])
            continue
        match = _ADDED_LINE.match(line)
        if match and current:
            added_by_path[current].append(match.group(1))
            text = match.group(1)
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    report.add("secret_detected", "Added content resembles a credential or private key.", path=current)
                    break
    for path, lines in added_by_path.items():
        suffix = Path(path).suffix.casefold()
        content = "\n".join(lines)
        if suffix == ".py" and content.strip():
            try:
                ast.parse(content)
            except SyntaxError:
                # Added hunks are not necessarily a complete module.  This is a
                # warning here; PatchManager performs a full candidate check when
                # a temporary worktree is available.
                report.add("python_parse_warning", "Added Python hunk is not independently parseable.", severity="warn", path=path)
        elif suffix == ".json" and content.strip():
            try:
                json.loads(content)
            except json.JSONDecodeError:
                report.add("json_parse_warning", "Added JSON hunk is not independently parseable.", severity="warn", path=path)
        elif suffix == ".toml" and content.strip():
            try:
                tomllib.loads(content)
            except tomllib.TOMLDecodeError:
                report.add("toml_parse_warning", "Added TOML hunk is not independently parseable.", severity="warn", path=path)
    return report


def assert_safe_patch(patch: str, files: list[str] | None = None) -> GuardrailReport:
    report = scan_patch(patch, files)
    if report.status == "block":
        details = "; ".join(f"{item.code}: {item.message}" for item in report.findings)
        raise ValueError(f"Guardrail preflight blocked the proposal: {details}")
    return report


def check_candidate_syntax(workspace: Path, patch: str, files: list[str]) -> GuardrailReport:
    """Reconstruct the candidate in a temporary directory and parse it safely."""
    report = GuardrailReport(checks=["candidate_parse"])
    with tempfile.TemporaryDirectory(prefix="juma-preflight-") as directory:
        temporary = Path(directory)
        for relative in files:
            source = workspace / relative
            target = temporary / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        completed = subprocess.run(
            ["git", "apply", "--ignore-whitespace", "--whitespace=nowarn", "-"],
            cwd=temporary,
            input=patch,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode:
            report.add("candidate_apply", "The candidate could not be reconstructed in preflight.")
            return report
        for relative in files:
            target = temporary / relative
            if not target.is_file():
                continue
            try:
                content = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                suffix = target.suffix.casefold()
                if suffix == ".py":
                    ast.parse(content)
                elif suffix == ".json":
                    json.loads(content)
                elif suffix == ".toml":
                    tomllib.loads(content)
            except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                report.add("candidate_parse", f"Candidate file failed syntax parsing: {exc}", path=relative)
    return report
