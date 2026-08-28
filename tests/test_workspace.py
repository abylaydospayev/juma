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


def test_strict_function_schemas_require_all_declared_properties() -> None:
    for tool in WORKSPACE_TOOLS:
        properties = tool["parameters"]["properties"]
        assert set(tool["parameters"]["required"]) == set(properties)
