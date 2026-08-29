from pathlib import Path

import pytest

from juma.workspace import WORKSPACE_TOOLS, WorkspaceTools


def test_workspace_tools_are_read_only_and_confined(tmp_path: Path) -> None:
    source = tmp_path / "example.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    tools = WorkspaceTools(tmp_path)

    assert tools.read_file("example.py")["content"] == "print('hello')\n"
    assert tools.search_files("hello")["matches"][0]["path"] == "example.py"
    with pytest.raises(ValueError):
        tools.read_file("../outside.txt")


def test_workspace_tools_exclude_runtime_directories(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    with pytest.raises(ValueError):
        WorkspaceTools(tmp_path).list_files(".venv")


def test_workspace_tools_exclude_environment_secrets(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("JUMA_API_TOKEN=secret\n", encoding="utf-8")

    with pytest.raises(ValueError):
        WorkspaceTools(tmp_path).read_file(".env")
    with pytest.raises(ValueError):
        WorkspaceTools(tmp_path).read_file(".env.local")
    files = WorkspaceTools(tmp_path).list_files()["files"]
    assert all(item not in {".env", ".env.local"} for item in files)


def test_strict_function_schemas_require_all_declared_properties() -> None:
    for tool in WORKSPACE_TOOLS:
        properties = tool["parameters"]["properties"]
        assert set(tool["parameters"]["required"]) == set(properties)
