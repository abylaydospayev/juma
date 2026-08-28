from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Protocol

from openai import OpenAI

from .config import Settings
from .state import AgentName, ProposedAction
from .workspace import WORKSPACE_TOOLS, WorkspaceTools

INSPECTION_TERMS = (
    "inspect",
    "review",
    "explain the architecture",
    "explain architecture",
    "run the tests",
    "run tests",
    "test suite",
    "pytest",
)
CODE_CHANGE_TERMS = (
    "add",
    "build",
    "change",
    "create",
    "edit",
    "fix",
    "implement",
    "modify",
    "refactor",
    "update",
)


class JumaModelError(RuntimeError):
    """Base error for model configuration and provider failures."""


class ModelConfigurationError(JumaModelError):
    """Raised when juma cannot authenticate with its configured model provider."""


class ModelProviderError(JumaModelError):
    """Raised when the provider rejects or fails a model request."""


class PatchGenerationError(JumaModelError):
    """Raised when a requested coding change has no safe unified diff."""


class ModelClient(Protocol):
    def generate(
        self,
        crew: AgentName,
        request: str,
        *,
        proposed_action: ProposedAction | None = None,
    ) -> str: ...


class RoutingModel(Protocol):
    def route(self, request: str) -> dict[str, str | float]: ...


CREW_INSTRUCTIONS: dict[AgentName, str] = {
    "coding": (
        "You are juma's coding crew. Answer the user's software task directly and precisely. "
        "You can inspect the configured workspace with read-only tools and run fixed checks. "
        "Use those tools when they improve accuracy. You may propose changes, but you cannot "
        "write files, commit, push, or deploy. Never claim that you did any of those things. "
        "When an action requires approval, present the plan as pending approval. "
        "For any requested code change, inspect the workspace first, then produce a proposed "
        "unified diff inside <juma-patch> and </juma-patch> tags. The diff must use paths relative "
        "to the workspace and must be complete enough for git apply. Never apply the patch "
        "yourself; the safety gate handles that after approval. "
        "Every file section must start with diff --git. Represent new files with new file mode, "
        "--- /dev/null, +++ b/path, and a valid hunk header. Never use *** Add File or *** Update "
        "File markers. "
        "Choose the smallest conventional implementation that satisfies the request. If the "
        "requested component does not yet exist, create it with focused tests instead of asking "
        "for clarification, unless the requirements conflict. "
        "For an inspection, architecture explanation, diagnosis, or test request, the user's "
        "request is already a complete task: do not ask what software change they want. "
        "Use the workspace tools before answering. For project inspection, list the workspace "
        "and read the relevant README, configuration, and source files. For a test request, "
        "call run_checks with check='tests' and report its return code and output. Return a "
        "clear report with Summary, Architecture or Findings, Evidence, Test Results, and "
        "Changes Made. If no change was requested, explicitly say that no files were changed."
    ),
    "research": (
        "You are juma's research crew. Give a clear, evidence-conscious synthesis that directly "
        "answers the request. Use live web search for current or source-sensitive claims and cite "
        "the sources in the answer. Do not invent citations. Use "
        "standard Markdown. Write inline math as $...$ and display math as $$...$$; never use "
        r"\(...\) or \[...\]. Check every equation for valid LaTeX and explicit relation symbols."
    ),
    "admin": (
        "You are juma's admin crew. Help draft and organize email, calendar, and workplace tasks. "
        "You have no connected accounts in this call. Never claim to have sent, posted, invited, "
        "or scheduled anything. External actions remain drafts pending human approval."
    ),
}


class OpenAIResponsesModel:
    """OpenAI Responses API adapter used by all juma crews."""

    def __init__(self, settings: Settings, *, client: Any | None = None):
        self.settings = settings
        self._client = client
        self.last_usage: dict[str, int] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise ModelConfigurationError(
                    "OPENAI_API_KEY is not set. Add it to the current PowerShell session "
                    "or to juma's .env file."
                )
            self._client = OpenAI(timeout=self.settings.request_timeout)
        return self._client

    def generate(
        self,
        crew: AgentName,
        request: str,
        *,
        proposed_action: ProposedAction | None = None,
    ) -> str:
        self.last_usage = {}
        instructions = CREW_INSTRUCTIONS[crew]
        if proposed_action:
            instructions += (
                " The safety layer classified this request as a "
                f"{proposed_action['risk']}-risk {proposed_action['kind']} action."
            )
        tools: list[dict[str, Any]] = []
        if crew == "research" and self.settings.enable_web_search:
            tools.append(
                {
                    "type": "web_search",
                    "external_web_access": True,
                    "search_context_size": "medium",
                }
            )
        if crew == "coding":
            tools.extend(WORKSPACE_TOOLS)

        if crew == "coding" and self._is_inspection_request(request):
            instructions += (
                " The request matches an inspection workflow. Do not finish with a clarification "
                "question. Gather evidence first, then provide the requested report."
            )

        workspace = WorkspaceTools(self.settings.resolved_workspace_root)
        if crew == "coding":
            preflight = self._workspace_preflight(request, workspace)
            if preflight:
                request += "\n\nPreflight evidence collected by juma:\n" + "\n".join(preflight)

        structured_patch = crew == "coding" and self._is_code_change_request(request)
        if structured_patch:
            instructions += (
                " Return a JSON object matching the requested code-change schema. Put the concise "
                "answer in response and the complete plain unified diff in patch. The patch must "
                "be directly consumable by git apply; do not include Markdown fences or prose in "
                "the patch string. Every section must begin with diff --git, including new files. "
                "Never leave patch empty for a requested change."
            )
        text_format = self._patch_response_format() if structured_patch else None

        transcript: list[Any] = [{"role": "user", "content": request}]
        response = self._create_response(
            instructions=instructions,
            input=request,
            tools=tools,
            text_format=text_format,
        )
        calls: list[Any] = []
        for _ in range(max(1, self.settings.max_tool_rounds)):
            calls = [
                item
                for item in getattr(response, "output", [])
                if self._type(item) == "function_call"
            ]
            if not calls:
                break
            transcript.extend(getattr(response, "output", []))
            outputs = []
            for call in calls:
                name = self._get(call, "name", "")
                try:
                    arguments = json.loads(self._get(call, "arguments", "{}"))
                    result = workspace.execute(name, arguments)
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    result = {"error": f"Invalid tool call: {exc}"}
                outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": self._get(call, "call_id", ""),
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
            transcript.extend(outputs)
            response = self._create_response(
                instructions=instructions,
                input=transcript,
                tools=tools,
                text_format=text_format,
            )

        if calls:
            response = self._create_response(
                instructions=(
                    instructions
                    + " You have enough evidence. Do not call tools now; write the final report "
                    "from the collected tool results."
                ),
                input=transcript,
                tools=[],
                text_format=text_format,
            )

        output = self._output_text(response).strip()
        if not output:
            raise ModelProviderError("OpenAI returned no text output.")
        if structured_patch:
            output = self._normalize_patch_response(output)
        sources = self._sources(response)
        if sources and "Sources" not in output:
            output += "\n\n### Sources\n" + "\n".join(
                f"- [{title}]({url})" for title, url in sources
            )
        return output

    def route(self, request: str) -> dict[str, str | float]:
        self.last_usage = {}
        response = self._create_response(
            instructions=(
                "You are juma's router. Select exactly one crew for the user's request. "
                "coding handles software and workspace tasks; research handles questions, "
                "papers, and current information; admin handles email, calendar, and Slack. "
                "Return only the requested JSON object."
            ),
            input=request,
            tools=[],
            text_format={
                "type": "json_schema",
                "name": "juma_route",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "target_agent": {
                            "type": "string",
                            "enum": ["coding", "research", "admin"],
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reason": {"type": "string"},
                    },
                    "required": ["target_agent", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
        )
        try:
            decision = json.loads(response.output_text)
            if decision["target_agent"] not in {"coding", "research", "admin"}:
                raise ValueError("Unknown crew")
            return {
                "target_agent": decision["target_agent"],
                "confidence": max(0.0, min(1.0, float(decision["confidence"]))),
                "reason": str(decision["reason"]),
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelProviderError(f"Router returned invalid structured output: {exc}") from exc

    def _create_response(
        self,
        *,
        instructions: str,
        input: Any,
        tools: list[dict[str, Any]],
        text_format: dict[str, Any] | None = None,
    ) -> Any:
        arguments: dict[str, Any] = {
            "model": self.settings.openai_model,
            "instructions": instructions,
            "input": input,
            "reasoning": {"effort": self.settings.openai_reasoning_effort},
            "max_output_tokens": self.settings.openai_max_output_tokens,
        }
        if tools:
            arguments["tools"] = tools
        if text_format:
            arguments["text"] = {"format": text_format}
        if any(tool.get("type") == "web_search" for tool in tools):
            arguments["include"] = ["web_search_call.action.sources"]
            arguments["max_tool_calls"] = max(1, self.settings.max_tool_rounds)
        last_error: Exception | None = None
        for attempt in range(max(0, self.settings.max_retries) + 1):
            try:
                response = self._get_client().responses.create(**arguments)
                usage = self._get(response, "usage")
                if usage is not None:
                    for key in ("input_tokens", "output_tokens", "total_tokens"):
                        value = self._get(usage, key)
                        if value is not None:
                            self.last_usage[key] = self.last_usage.get(key, 0) + int(value or 0)
                return response
            except JumaModelError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.max_retries:
                    time.sleep(0.25 * (attempt + 1))
        raise ModelProviderError(
            f"OpenAI request failed after retries: {last_error}"
        ) from last_error

    @staticmethod
    def _get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    @staticmethod
    def _is_inspection_request(request: str) -> bool:
        lowered = OpenAIResponsesModel._current_request(request).casefold()
        return any(term in lowered for term in INSPECTION_TERMS)

    @staticmethod
    def _is_code_change_request(request: str) -> bool:
        current = OpenAIResponsesModel._current_request(request).casefold()
        if "update me" in current or "give me an update" in current:
            return False
        words = set(re.findall(r"[a-z]+", current))
        return bool(words & set(CODE_CHANGE_TERMS))

    @staticmethod
    def _patch_response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "name": "juma_code_change",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "response": {"type": "string"},
                    "patch": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "A complete plain unified diff with workspace-relative paths."
                        ),
                    },
                },
                "required": ["response", "patch"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def _normalize_patch_response(output: str) -> str:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return output
        if not isinstance(payload, dict) or "patch" not in payload:
            return output
        answer = str(payload.get("response", "")).strip()
        patch = str(payload.get("patch", "")).strip()
        if not patch:
            return answer
        return f"{answer}\n\n<juma-patch>\n{patch}\n</juma-patch>".strip()

    @staticmethod
    def _current_request(request: str) -> str:
        return request.split("\nRelevant conversation context:", 1)[0]

    @classmethod
    def _workspace_preflight(cls, request: str, workspace: WorkspaceTools) -> list[str]:
        current = cls._current_request(request).casefold()
        evidence: list[str] = []
        if any(term in current for term in ("inspect", "review", "architecture")):
            evidence.append("LIST_FILES: " + json.dumps(workspace.list_files("")))
        if any(term in current for term in ("run the tests", "run tests", "test suite", "pytest")):
            evidence.append("RUN_CHECKS: " + json.dumps(workspace.run_checks("tests")))
        return evidence

    @classmethod
    def _type(cls, item: Any) -> str:
        return str(cls._get(item, "type", ""))

    @classmethod
    def _output_text(cls, response: Any) -> str:
        output_text = cls._get(response, "output_text", "") or ""
        if output_text:
            return str(output_text)
        parts: list[str] = []
        for item in cls._get(response, "output", []) or []:
            if cls._type(item) != "message":
                continue
            for content in cls._get(item, "content", []) or []:
                if cls._type(content) == "output_text":
                    text = cls._get(content, "text", "")
                    if text:
                        parts.append(str(text))
        return "\n".join(parts)

    @classmethod
    def _sources(cls, response: Any) -> list[tuple[str, str]]:
        sources: list[tuple[str, str]] = []
        for item in getattr(response, "output", []):
            action = cls._get(item, "action")
            for source in cls._get(action, "sources", []) or []:
                url = cls._get(source, "url")
                title = cls._get(source, "title", url)
                if url and (title, url) not in sources:
                    sources.append((title, url))
        return sources[:12]
