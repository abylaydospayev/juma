from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


JUMA_OPENAI_MODEL = "gpt-5.6-luna"


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    checkpoint_db: Path
    memory_db: Path
    # Juma's production reasoning path is intentionally locked to Luna.  The
    # voice models below are separate specialist models and are not part of
    # this general-purpose model selection.
    openai_model: str = JUMA_OPENAI_MODEL
    openai_reasoning_effort: str = "medium"
    openai_max_output_tokens: int = 4096
    enable_web_search: bool = True
    max_tool_rounds: int = 6
    max_retries: int = 2
    request_timeout: float = 120.0
    workspace_root: Path | None = None
    voice_enabled: bool = False
    voice_transcription_model: str = "gpt-4o-mini-transcribe"
    voice_speech_model: str = "gpt-4o-mini-tts"
    voice_name: str = "alloy"
    server_host: str = "127.0.0.1"
    server_port: int = 8000
    auto_repair: bool = False
    max_repair_attempts: int = 3
    auto_commit: bool = False
    auto_push: bool = False
    push_remote: str = "origin"
    auto_setup_environment: bool = False
    environment_timeout: float = 600.0
    # Production mode is deliberately conservative: approved work is tested in
    # place, but repair, commit, push, and deployment adapters remain disabled.
    # Directly-constructed Settings are treated as local development for
    # backwards compatibility; ``from_env`` defaults hosted deployments to true.
    production_mode: bool = False
    workspace_id: str = "default"
    api_max_body_bytes: int = 1_000_000
    runner_timeout: int = 600

    def __post_init__(self) -> None:
        if self.openai_model != JUMA_OPENAI_MODEL:
            raise ValueError(
                f"Juma is locked to {JUMA_OPENAI_MODEL}; got {self.openai_model!r}."
            )

    @property
    def resolved_workspace_root(self) -> Path:
        return (self.workspace_root or Path.cwd()).resolve()

    @property
    def audit_log(self) -> Path:
        return self.data_dir / "audit.jsonl"

    @property
    def preferences_db(self) -> Path:
        return self.data_dir / "preferences.sqlite"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        data_dir = Path(os.getenv("JUMA_DATA_DIR", "data")).resolve()
        configured_model = os.getenv("JUMA_OPENAI_MODEL")
        if configured_model and configured_model != JUMA_OPENAI_MODEL:
            raise ValueError(
                f"Juma only supports {JUMA_OPENAI_MODEL}; "
                f"remove JUMA_OPENAI_MODEL or set it to {JUMA_OPENAI_MODEL}."
            )
        return cls(
            data_dir=data_dir,
            checkpoint_db=data_dir / "checkpoints.sqlite",
            memory_db=data_dir / "memory.sqlite",
            openai_model=JUMA_OPENAI_MODEL,
            openai_reasoning_effort=os.getenv("JUMA_REASONING_EFFORT", "medium"),
            openai_max_output_tokens=int(os.getenv("JUMA_MAX_OUTPUT_TOKENS", "4096")),
            enable_web_search=os.getenv("JUMA_ENABLE_WEB_SEARCH", "true").lower()
            in {"1", "true", "yes", "on"},
            max_tool_rounds=int(os.getenv("JUMA_MAX_TOOL_ROUNDS", "6")),
            max_retries=int(os.getenv("JUMA_MAX_RETRIES", "2")),
            request_timeout=float(os.getenv("JUMA_REQUEST_TIMEOUT", "120")),
            workspace_root=(
                Path(os.environ["JUMA_WORKSPACE_ROOT"]).resolve()
                if os.getenv("JUMA_WORKSPACE_ROOT")
                else None
            ),
            voice_enabled=os.getenv("JUMA_VOICE_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"},
            voice_transcription_model=os.getenv(
                "JUMA_VOICE_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"
            ),
            voice_speech_model=os.getenv("JUMA_VOICE_SPEECH_MODEL", "gpt-4o-mini-tts"),
            voice_name=os.getenv("JUMA_VOICE_NAME", "alloy"),
            server_host=os.getenv("JUMA_SERVER_HOST", "127.0.0.1"),
            server_port=int(os.getenv("JUMA_SERVER_PORT", "8000")),
            auto_repair=os.getenv("JUMA_AUTO_REPAIR", "false").lower()
            in {"1", "true", "yes", "on"},
            max_repair_attempts=max(0, int(os.getenv("JUMA_MAX_REPAIR_ATTEMPTS", "3"))),
            auto_commit=os.getenv("JUMA_AUTO_COMMIT", "false").lower()
            in {"1", "true", "yes", "on"},
            auto_push=os.getenv("JUMA_AUTO_PUSH", "false").lower()
            in {"1", "true", "yes", "on"},
            push_remote=os.getenv("JUMA_PUSH_REMOTE", "origin"),
            auto_setup_environment=os.getenv("JUMA_AUTO_SETUP", "false").lower()
            in {"1", "true", "yes", "on"},
            environment_timeout=float(os.getenv("JUMA_ENVIRONMENT_TIMEOUT", "600")),
            production_mode=os.getenv("JUMA_PRODUCTION", "true").lower()
            in {"1", "true", "yes", "on"},
            workspace_id=os.getenv("JUMA_WORKSPACE_ID", "default"),
            api_max_body_bytes=max(16_384, int(os.getenv("JUMA_API_MAX_BODY_BYTES", "1000000"))),
            runner_timeout=max(30, int(os.getenv("JUMA_RUNNER_TIMEOUT", "600"))),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
