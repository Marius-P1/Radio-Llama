"""The text-to-speech interface every backend implements."""

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
