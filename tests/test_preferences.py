from pathlib import Path

import pytest

from juma.preferences import PreferenceStore


def test_preferences_are_durable_and_replaceable(tmp_path: Path) -> None:
    path = tmp_path / "preferences.sqlite"
    first = PreferenceStore(path)
    assert first.set("style", "concise") == {"key": "style", "value": "concise"}
    first.close()

    second = PreferenceStore(path)
    assert second.all() == {"style": "concise"}
    assert second.set("style", "warm") == {"key": "style", "value": "warm"}
    assert second.get("style") == "warm"
    assert second.delete("style") is True
    assert second.get("style") is None
    second.close()


@pytest.mark.parametrize("key", ["", " " , "x" * 81])
def test_preference_keys_are_bounded(tmp_path: Path, key: str) -> None:
    store = PreferenceStore(tmp_path / "preferences.sqlite")
    try:
        with pytest.raises(ValueError, match="keys"):
            store.set(key, "value")
    finally:
        store.close()


def test_preference_values_are_bounded(tmp_path: Path) -> None:
    store = PreferenceStore(tmp_path / "preferences.sqlite")
    try:
        with pytest.raises(ValueError, match="values"):
            store.set("style", "x" * 4001)
    finally:
        store.close()
