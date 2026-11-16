"""The text-to-speech interface every backend implements, and the list of voices to pick from."""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class Voice(BaseModel):
    voice_id: str
    model_id: str
    # ElevenLabs voice settings such as stability, similarity_boost
    settings: dict[str, float] = Field(default_factory=dict)


class TTSError(Exception):
    """A text-to-speech call failed. The message is readable and names the backend."""


class TextToSpeech(Protocol):
    def synthesize(self, text: str, voice: Voice) -> Path: ...


class VoiceOption(BaseModel):
    """One voice the listener can give the host (D040). Sent to the web app as is."""

    id: str  # the voice id the TTS backend takes
    name: str
    gender: str | None = None
    accent: str | None = None
    age: str | None = None
    descriptive: str | None = None  # one word about the voice, for example "warm"
    language: str | None = None  # the voice's native language, for example "en"
    # Language codes the voice is verified for, each once.
    languages: list[str] = Field(default_factory=list)
    preview_url: str | None = None  # the default sample, a URL the browser plays as is
    # A sample per language code, for the languages that have one.
    previews: dict[str, str] = Field(default_factory=dict)


class VoiceList(Protocol):
    """The voices a backend offers. A failure raises TTSError."""

    def list_voices(self) -> list[VoiceOption]: ...


class UnknownVoiceError(LookupError):
    """The picked voice is not in the voice list. The message names the id."""


def find_voice(voices: VoiceList, voice_id: str | None) -> VoiceOption | None:
    """The listed voice with `voice_id`, checked the same way by the API and the CLI (D040).

    A blank id means no voice was picked: the answer is None and the list is not read.
    Raises UnknownVoiceError when no listed voice has the id, and TTSError when the list
    cannot be read.
    """
    voice_id = (voice_id or "").strip()
    if not voice_id:
        return None
    for voice in voices.list_voices():
        if voice.id == voice_id:
            return voice
    raise UnknownVoiceError(
        f"Unknown voice {voice_id!r}. Pick one of the listed voices, or leave the voice "
        "out to use the style's own voice."
    )
