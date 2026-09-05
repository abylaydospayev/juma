"""Narrow host-side broker for disposable, no-network checks."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunnerJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    workspace: Path
    command: list[str] = Field(min_length=1, max_length=32)
    image: str
    timeout_seconds: int = Field(default=600, ge=1, le=600)


class HostBroker:
    """Validate a fixed job schema before invoking Docker on the host."""

    def __init__(self, *, allowed_root: Path, pinned_image: str):
        self.allowed_root = allowed_root.resolve()
        self.pinned_image = pinned_image
        if "@sha256:" not in pinned_image:
            raise ValueError("Runner image must be digest pinned.")

    def validate(self, job: RunnerJob) -> RunnerJob:
        workspace = job.workspace.resolve()
        try:
            workspace.relative_to(self.allowed_root)
        except ValueError as exc:
            raise ValueError("Runner worktree is outside the allowed root.") from exc
        if job.image != self.pinned_image:
            raise ValueError("Runner image is not the configured digest.")
        if any(not isinstance(arg, str) or len(arg) > 400 for arg in job.command):
            raise ValueError("Runner command contains an invalid argument.")
        return job.model_copy(update={"workspace": workspace})

    def run(self, job: RunnerJob) -> dict[str, Any]:
        job = self.validate(job)
        command = [
            "docker", "run", "--rm", "--network=none", "--read-only",
            "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--user=65532:65532", "--cpus=1", "--memory=1g", "--pids-limit=256",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=256m", "--mount",
            f"type=bind,src={job.workspace},dst=/workspace,ro", job.image, *job.command,
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=job.timeout_seconds, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"job_id": job.job_id, "status": "failed", "error": str(exc)}
        return {
            "job_id": job.job_id,
            "status": "succeeded" if completed.returncode == 0 else "failed",
            "return_code": completed.returncode,
            "output": (completed.stdout + completed.stderr)[-12_000:],
        }


def main() -> None:
    job = RunnerJob.model_validate_json(sys.stdin.read())
    broker = HostBroker(allowed_root=Path("/srv/juma/jobs"), pinned_image=job.image)
    print(json.dumps(broker.run(job)))
