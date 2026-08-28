from pathlib import Path
from types import SimpleNamespace

from juma.config import Settings
from juma.models import OpenAIResponsesModel


class FakeResponses:
    def __init__(self) -> None:
        self.arguments = None

    def create(self, **kwargs):
        self.arguments = kwargs
        return SimpleNamespace(output_text="  A real model response.  ")


class RoutingResponses:
    def create(self, **kwargs):
        return SimpleNamespace(
            output_text='{"target_agent":"coding","confidence":0.94,"reason":"software task"}'
        )


class WorkspaceResponses:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="function_call",
                        name="list_files",
                        arguments='{"directory":""}',
                        call_id="call-1",
                    )
                ],
                output_text="",
            )
        return SimpleNamespace(output=[], output_text="The workspace is readable.")


class ExhaustedWorkspaceResponses:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("tools"):
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[SimpleNamespace(type="output_text", text="Final report.")],
                    )
                ],
                output_text="",
            )
        return SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="list_files",
                    arguments='{"directory":""}',
                    call_id=f"call-{len(self.calls)}",
                )
            ],
            output_text="",
        )


class StructuredPatchResponses:
    def create(self, **kwargs):
        return SimpleNamespace(
            output_text=(
                '{"response":"I prepared the requested change.",'
                '"patch":"--- a/src/app.py\\n+++ b/src/app.py\\n@@ -1 +1 @@\\n-old\\n+new"}'
            )
        )


def test_openai_responses_adapter_uses_luna(tmp_path: Path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "checkpoints.sqlite",
        tmp_path / "memory.sqlite",
    )
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    model = OpenAIResponsesModel(settings, client=client)

    output = model.generate("research", "Explain agent memory")

    assert output == "A real model response."
    assert responses.arguments["model"] == "gpt-5.6-luna"
    assert responses.arguments["reasoning"] == {"effort": "medium"}
    assert responses.arguments["input"] == "Explain agent memory"
    assert responses.arguments["tools"][0]["type"] == "web_search"


def test_openai_adapter_supports_structured_routing(tmp_path: Path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "checkpoints.sqlite",
        tmp_path / "memory.sqlite",
    )
    model = OpenAIResponsesModel(settings, client=SimpleNamespace(responses=RoutingResponses()))

    assert model.route("inspect the Python project") == {
        "target_agent": "coding",
        "confidence": 0.94,
        "reason": "software task",
    }


def test_openai_adapter_executes_only_declared_workspace_tools(tmp_path: Path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "checkpoints.sqlite",
        tmp_path / "memory.sqlite",
        workspace_root=tmp_path,
    )
    responses = WorkspaceResponses()
    model = OpenAIResponsesModel(settings, client=SimpleNamespace(responses=responses))

    assert model.generate("coding", "inspect the project") == "The workspace is readable."
    assert responses.calls[0]["tools"][-1]["name"] == "run_checks"
    assert responses.calls[1]["input"][-1]["type"] == "function_call_output"


def test_openai_adapter_finishes_after_tool_budget(tmp_path: Path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "checkpoints.sqlite",
        tmp_path / "memory.sqlite",
        max_tool_rounds=1,
        workspace_root=tmp_path,
    )
    responses = ExhaustedWorkspaceResponses()
    model = OpenAIResponsesModel(settings, client=SimpleNamespace(responses=responses))

    assert model.generate("coding", "inspect the project") == "Final report."
    assert responses.calls[-1].get("tools", []) == []


def test_openai_adapter_normalizes_structured_coding_patch(tmp_path: Path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "checkpoints.sqlite",
        tmp_path / "memory.sqlite",
        workspace_root=tmp_path,
    )
    responses = StructuredPatchResponses()
    model = OpenAIResponsesModel(settings, client=SimpleNamespace(responses=responses))

    output = model.generate("coding", "add a health endpoint")

    assert "I prepared the requested change." in output
    assert "<juma-patch>" in output
    assert "--- a/src/app.py" in output
