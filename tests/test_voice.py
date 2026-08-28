from pathlib import Path
from types import SimpleNamespace

from juma.config import Settings
from juma.voice import VoiceService


class FakeTranscriptions:
    def create(self, **kwargs):
        assert kwargs["model"] == "transcribe-model"
        assert kwargs["file"][1] == b"audio"
        return SimpleNamespace(text="research this")


class FakeSpeech:
    def create(self, **kwargs):
        assert kwargs["model"] == "speech-model"
        assert kwargs["voice"] == "alloy"
        return SimpleNamespace(content=b"mp3-audio")


def test_voice_service_transcribes_and_synthesizes(tmp_path: Path) -> None:
    settings = Settings(
        tmp_path,
        tmp_path / "checkpoints.sqlite",
        tmp_path / "memory.sqlite",
        voice_transcription_model="transcribe-model",
        voice_speech_model="speech-model",
    )
    client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=FakeTranscriptions(),
            speech=FakeSpeech(),
        )
    )
    voice = VoiceService(settings, client=client)

    assert voice.transcribe(b"audio") == "research this"
    assert voice.synthesize("Hello") == b"mp3-audio"
    assert voice.synthesize_to_file("Hello", tmp_path / "reply.mp3").read_bytes() == b"mp3-audio"
