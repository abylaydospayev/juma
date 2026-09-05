"""Deterministic offline benchmark skeleton; live mode is explicitly cost-capped."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any


def run_benchmark(fixtures: Path, *, repetitions: int = 1, live: bool = False, max_cost_usd: float | None = None) -> dict[str, Any]:
    if live and (max_cost_usd is None or max_cost_usd <= 0):
        raise ValueError("Live benchmarks require --max-cost-usd > 0.")
    cases = sorted(fixtures.glob("*.json")) if fixtures.exists() else []
    latencies: list[float] = []
    for _ in range(max(1, repetitions)):
        for case in cases:
            started = time.perf_counter()
            json.loads(case.read_text(encoding="utf-8"))
            latencies.append((time.perf_counter() - started) * 1000)
    return {
        "suite": str(fixtures),
        "mode": "live" if live else "offline",
        "cases": len(cases),
        "repetitions": max(1, repetitions),
        "latency_ms": {
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": max(latencies) if latencies else 0.0,
        },
        "valid_patch_rate": None,
        "first_test_pass_rate": None,
        "routing_accuracy": None,
        "end_to_end_success": None,
        "cost_cap_usd": max_cost_usd,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=Path("bench/fixtures"))
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-cost-usd", type=float)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(args.fixtures, repetitions=args.repetitions, live=args.live, max_cost_usd=args.max_cost_usd), indent=2))
