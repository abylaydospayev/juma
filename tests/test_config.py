from pathlib import Path

from juma.config import Settings


def test_server_settings_can_be_configured_from_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JUMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("JUMA_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("JUMA_SERVER_PORT", "8510")

    settings = Settings.from_env()

    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 8510
