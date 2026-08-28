from pathlib import Path

from juma.memory import MemoryStore


def test_memory_scope(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.sqlite")
    try:
        store.remember("coding", "shared result")
        store.remember("coding", "private detail", scope="private")
        assert len(store.search("result", crew="admin")) == 1
        assert store.search("detail", crew="admin") == []
        assert len(store.search("detail", crew="coding")) == 1
    finally:
        store.close()
