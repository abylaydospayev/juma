from pathlib import Path

from juma.context import ContextService
from juma.memory import MemoryStore
from juma.preferences import PreferenceStore


def test_context_precedence_and_workspace_isolation(tmp_path: Path) -> None:
    memory = MemoryStore(tmp_path / "memory.sqlite")
    prefs = PreferenceStore(tmp_path / "preferences.sqlite")
    prefs.set("style", "global")
    prefs.set("style", "workspace", scope_type="workspace", scope_id="ws-1")
    memory.remember("coding", "workspace-only fact", workspace_id="ws-1")
    service = ContextService(memory, prefs)
    bundle = service.get_context("workspace-only", "ws-1", "thread-1", "coding")
    assert bundle["preferences"]["style"] == "workspace"
    assert bundle["memories"][0]["content"] == "workspace-only fact"
    other = service.get_context("workspace-only", "ws-2", "thread-2", "coding")
    assert not other["memories"]
    memory.close()
    prefs.close()
