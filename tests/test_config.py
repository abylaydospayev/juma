from pathlib import Path

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
