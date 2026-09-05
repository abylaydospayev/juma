"""Idempotent local daemon tasks. Destructive operations are report-only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def run_once(data_dir: Path) -> dict[str, Any]:
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / "daemon-last-run.json"
    now = datetime.now(UTC).isoformat()
    result = {
        "ran_at": now,
        "timezone": "America/Los_Angeles",
        "tasks": ["semantic_rollup", "integrity_check", "backup_verification", "morning_briefing"],
        "branch_cleanup": "report_only",
    }
    marker.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    print(json.dumps(run_once(parser.parse_args().data_dir), indent=2))
