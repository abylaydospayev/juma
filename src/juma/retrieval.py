"""Safe lexical retrieval with bounded, line-addressed snippets."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MAX_READ_BYTES = 200_000
IGNORED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "data"}


def is_ignored_part(part: str) -> bool:
    return part in IGNORED_PARTS or part == ".env" or part.startswith(".env.")


class RetrievalService:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def retrieve(self, query: str, *, limit_candidates: int = 50, limit_snippets: int = 5) -> dict[str, Any]:
        tokens = set(re.findall(r"[\w-]{2,}", query.casefold()))
        if not tokens:
            return {"candidates": [], "snippets": [], "strategy": "lexical-v1"}
        candidates: list[tuple[float, str, int, str]] = []
        for path in self.root.rglob("*"):
            if not path.is_file() or any(is_ignored_part(part) for part in path.relative_to(self.root).parts):
                continue
            try:
                if path.stat().st_size > MAX_READ_BYTES:
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for number, line in enumerate(lines, 1):
                overlap = len(tokens & set(re.findall(r"[\w-]{2,}", line.casefold())))
                if overlap:
                    candidates.append((float(overlap), str(path.relative_to(self.root)), number, line[:500]))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        bounded = candidates[: max(1, min(limit_candidates, 50))]
        snippets = [
            {"path": path, "line_start": line, "line_end": line, "text": text}
            for _, path, line, text in bounded[: max(1, min(limit_snippets, 5))]
        ]
        return {
            "strategy": "lexical-v1",
            "candidates": [{"path": path, "line": line, "score": score} for score, path, line, _ in bounded],
            "snippets": snippets,
        }
