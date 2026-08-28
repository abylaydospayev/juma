"""Optional speech input and output for Juma."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import Settings


class VoiceError(RuntimeError):
    """Raised when speech input or output cannot be completed."""


class VoiceService:
    """Use the configured OpenAI audio endpoints without making voice mandatory."""

    def __init__(self, settings: Settings, *, client: Any | None = None):
        self.settings = settings
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise VoiceError("OPENAI_API_KEY is not set; voice features are unavailable.")
            self._client = OpenAI(timeout=self.settings.request_timeout)
        return self._client

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "juma-recording.wav",
        content_type: str = "audio/wav",
    ) -> str:
        if not audio:
            raise VoiceError("The voice recording was empty.")
        try:
            result = self._get_client().audio.transcriptions.create(
                model=self.settings.voice_transcription_model,
                file=(filename, audio, content_type),
            )
        except Exception as exc:
            raise VoiceError(f"Speech transcription failed: {exc}") from exc
        text = result if isinstance(result, str) else getattr(result, "text", "")
        if not str(text).strip():
            raise VoiceError("Speech transcription returned no text.")
        return str(text).strip()

    def synthesize(self, text: str) -> bytes:
        if not text.strip():
            raise VoiceError("There is no response to speak.")
        try:
            response = self._get_client().audio.speech.create(
                model=self.settings.voice_speech_model,
                voice=self.settings.voice_name,
                input=text[:4000],
                response_format="mp3",
            )
            content = getattr(response, "content", None)
            if content is not None:
                return bytes(content)
            return bytes(response.read())
        except Exception as exc:
            raise VoiceError(f"Speech synthesis failed: {exc}") from exc

    def synthesize_to_file(self, text: str, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.synthesize(text))
        return output
