from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    checkpoint_db: Path
    memory_db: Path
    openai_model: str = "gpt-5.6-luna"
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
    auto_repair: bool = False
    max_repair_attempts: int = 3
    auto_commit: bool = False

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
        return cls(
            data_dir=data_dir,
            checkpoint_db=data_dir / "checkpoints.sqlite",
            memory_db=data_dir / "memory.sqlite",
            openai_model=os.getenv("JUMA_OPENAI_MODEL", "gpt-5.6-luna"),
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
            auto_repair=os.getenv("JUMA_AUTO_REPAIR", "false").lower()
            in {"1", "true", "yes", "on"},
            max_repair_attempts=max(0, int(os.getenv("JUMA_MAX_REPAIR_ATTEMPTS", "3"))),
            auto_commit=os.getenv("JUMA_AUTO_COMMIT", "false").lower()
            in {"1", "true", "yes", "on"},
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
