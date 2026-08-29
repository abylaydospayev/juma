from pathlib import Path

from juma.environment import ProjectEnvironment


def test_project_environment_prefers_existing_project_venv(tmp_path: Path) -> None:
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")

    result = ProjectEnvironment(tmp_path, auto_setup=True).prepare()

    assert result == {
        "status": "ready",
        "python": str(interpreter),
        "source": "project-venv",
        "created": False,
    }


def test_project_environment_can_use_juma_runtime_without_setup(tmp_path: Path) -> None:
    result = ProjectEnvironment(tmp_path).prepare()

    assert result["status"] == "ready"
    assert result["source"] == "juma-runtime"
    assert result["created"] is False


def test_project_environment_sets_up_a_temporary_venv(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    interpreter = Path("temporary-python")
    calls: list[list[str]] = []
    lookup_results = iter([None, interpreter])
    environment = ProjectEnvironment(tmp_path, auto_setup=True)
    monkeypatch.setattr(environment, "_existing_interpreter", lambda: None)
    monkeypatch.setattr(environment, "_interpreter", lambda _: next(lookup_results))
    monkeypatch.setattr(environment, "_run", lambda command: calls.append(command))

    result = environment.prepare()

    assert result == {
        "status": "ready",
        "python": str(interpreter),
        "source": "temporary-venv",
        "created": True,
    }
    assert calls[0][1:3] == ["-m", "venv"]
    assert calls[1][1:] == ["-m", "pip", "install", "-e", "."]
