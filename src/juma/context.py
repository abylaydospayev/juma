"""Bounded hierarchical context composition for crews and API clients."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .memory import MemoryStore
from .preferences import PreferenceStore


@dataclass(slots=True)
class ContextBundle:
    preferences: dict[str, str]
    memories: list[dict[str, Any]]
    inferences: list[dict[str, Any]]
    snippets: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    estimated_tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "preferences": self.preferences,
            "memories": self.memories,
            "inferences": self.inferences,
            "snippets": self.snippets,
            "provenance": self.provenance,
            "estimated_tokens": self.estimated_tokens,
        }


class ContextService:
    """Resolve Global -> Workspace -> Thread values without mixing visibility."""

    def __init__(self, memory: MemoryStore, preferences: PreferenceStore):
        self.memory = memory
        self.preferences = preferences

    def get_context(
        self,
        query: str,
        workspace_id: str,
        thread_id: str,
        crew: str,
        token_budget: int = 1200,
    ) -> dict[str, Any]:
        budget = max(128, min(token_budget, 12_000))
        resolved: dict[str, str] = {}
        preference_seen: set[str] = set()
        provenance: list[dict[str, Any]] = []
        for scope_type, scope_id in (
            ("thread", thread_id),
            ("workspace", workspace_id),
            ("global", "global"),
        ):
            entries = self.preferences.all(scope_type=scope_type, scope_id=scope_id, include_deleted=True)
            for key, entry in entries.items():
                value, deleted = entry
                if key in resolved or key in preference_seen:
                    continue
                preference_seen.add(key)
                if not deleted:
                    resolved[key] = value
                provenance.append({"kind": "preference", "key": key, "scope": scope_type, "scope_id": scope_id})

        memories: list[dict[str, Any]] = []
        seen: set[int] = set()
        for scope_type, scope_id in (
            ("thread", thread_id),
            ("workspace", workspace_id),
            ("global", "global"),
        ):
            rows = self.memory.search(
                query,
                crew=crew,
                workspace_id=workspace_id,
                thread_id=thread_id,
                limit=20,
            )
            for row in rows:
                if row["id"] in seen:
                    continue
                if row.get("scope_type", "global") != scope_type or row.get("scope_id", "global") != scope_id:
                    continue
                seen.add(row["id"])
                memories.append(row)
                provenance.append({"kind": "memory", "id": row["id"], "scope": scope_type, "scope_id": scope_id})

        accepted = [item for item in memories if item.get("memory_kind", "authored") == "authored"]
        inferences = [item for item in memories if item.get("memory_kind") == "inference" and item.get("status") == "accepted"]
        snippets: list[dict[str, Any]] = []
        # Keep the bundle bounded by a simple, deterministic character budget.
        used = 0
        for item in accepted + inferences:
            content = str(item.get("content", ""))[:800]
            cost = max(1, len(content) // 4)
            if used + cost > budget:
                break
            snippets.append({"source": "memory", "id": item["id"], "content": content})
            used += cost
        preference_text = " ".join(f"{key} {value}" for key, value in resolved.items())
        estimated = (len(query) + len(preference_text) + sum(len(item["content"]) for item in snippets)) // 4
        if estimated > budget:
            estimated = budget
        return ContextBundle(
            preferences=resolved,
            memories=accepted,
            inferences=inferences,
            snippets=snippets,
            provenance=provenance,
            estimated_tokens=estimated,
        ).as_dict()
