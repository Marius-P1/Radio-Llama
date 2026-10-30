from pathlib import Path
from typing import Any

import pytest
from elevenlabs import VoiceSettings

from radio_llama.voice import ElevenLabsTTS, TTSError, Voice


class StubTextToSpeech:
    def __init__(self, error: Exception | None = None) -> None:
        self.kwargs: dict[str, Any] | None = None
        self.error = error

    def convert(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return iter([b"ID3", b"fake-mp3-bytes"])


class StubClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.text_to_speech = StubTextToSpeech(error)


def make_tts(tmp_path: Path, error: Exception | None = None) -> tuple[ElevenLabsTTS, StubClient]:
    client = StubClient(error)
    return ElevenLabsTTS("key", tmp_path / "raw", client=client), client  # type: ignore[arg-type]


def test_writes_the_joined_chunks_and_maps_the_voice(tmp_path: Path) -> None:
    tts, client = make_tts(tmp_path)
    path = tts.synthesize("hello", Voice(voice_id="abc", model_id="eleven_multilingual_v2"))
    assert path.parent == tmp_path / "raw"
    assert path.suffix == ".mp3"
    assert path.read_bytes() == b"ID3fake-mp3-bytes"
    kwargs = client.text_to_speech.kwargs
    assert kwargs is not None
    assert kwargs["voice_id"] == "abc"
    assert kwargs["model_id"] == "eleven_multilingual_v2"
    assert kwargs["text"] == "hello"
    assert kwargs["output_format"] == "mp3_44100_128"
    assert kwargs["voice_settings"] is None


def test_settings_become_voice_settings(tmp_path: Path) -> None:
    tts, client = make_tts(tmp_path)
    voice = Voice(voice_id="abc", model_id="m", settings={"stability": 0.3, "style": 0.8})
    tts.synthesize("hello", voice)
    assert client.text_to_speech.kwargs is not None
    voice_settings = client.text_to_speech.kwargs["voice_settings"]
    assert isinstance(voice_settings, VoiceSettings)
    assert voice_settings.stability == 0.3
    assert voice_settings.style == 0.8


def test_sdk_error_becomes_tts_error(tmp_path: Path) -> None:
    tts, _ = make_tts(tmp_path, error=RuntimeError("quota exceeded"))
    with pytest.raises(TTSError, match="ElevenLabs") as info:
        tts.synthesize("hello", Voice(voice_id="abc", model_id="m"))
    assert "quota exceeded" in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)
