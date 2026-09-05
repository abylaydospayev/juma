from pathlib import Path

import pytest

from juma.config import Settings


def test_server_settings_can_be_configured_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JUMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JUMA_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("JUMA_SERVER_PORT", "8510")
    monkeypatch.setenv("JUMA_AUTO_PUSH", "true")
    monkeypatch.setenv("JUMA_PUSH_REMOTE", "upstream")
    monkeypatch.setenv("JUMA_AUTO_SETUP", "true")
    monkeypatch.setenv("JUMA_ENVIRONMENT_TIMEOUT", "45")

    settings = Settings.from_env()

    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 8510
    assert settings.auto_push is True
    assert settings.push_remote == "upstream"
    assert settings.auto_setup_environment is True
    assert settings.environment_timeout == 45


def test_general_reasoning_model_is_locked_to_luna(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JUMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JUMA_OPENAI_MODEL", "gpt-5.6-terra")

    with pytest.raises(ValueError, match="only supports gpt-5.6-luna"):
        Settings.from_env()


def test_general_reasoning_model_defaults_to_luna(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JUMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("JUMA_OPENAI_MODEL", raising=False)

    settings = Settings.from_env()

    assert settings.openai_model == "gpt-5.6-luna"


def test_direct_settings_cannot_select_another_general_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="locked to gpt-5.6-luna"):
        Settings(
            tmp_path,
            tmp_path / "checkpoints.sqlite",
            tmp_path / "memory.sqlite",
            openai_model="gpt-5.6-sol",
        )
