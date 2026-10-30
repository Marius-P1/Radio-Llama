"""Domain models.

These are the shapes of `catalog.json` (Track, Fact) and `show.json` (Show).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Fact(BaseModel):
    text: str
    source_url: str


class Track(BaseModel):
    id: str
    title: str
    artist: str
    style: str  # for example "jazz", "rock"
    duration_s: float  # full track length in seconds
    license: str  # SPDX-like id, for example "CC-BY-4.0" or "CC-BY-SA-4.0"
    source_url: str  # page where the track was found, used for credits
    audio_file: str  # file name inside catalog/audio/
    facts: list[Fact] = Field(default_factory=list)
    placeholder: bool = False  # True only for the fake tracks shipped before the real catalog


class Persona(BaseModel):
    id: str  # for example "calm"
    name: str  # display name
    description: str  # one or two sentences fed to the prompt
    voice_id: str  # ElevenLabs voice id


class ShowConfig(BaseModel):
    style: str
    persona: str  # Persona.id
    language: Literal["en", "fr"] = "en"
    generator: Literal["pipeline"] = "pipeline"  # D008: only "pipeline" in Phase 1
    track_count: int = 3
    song_length_s: float = 30.0  # how long each song plays before the fade-out
    seed: int | None = None  # fixes the random track pick, for reproducible runs (D017)


SegmentKind = Literal["intro", "song", "transition", "outro"]


class Segment(BaseModel):
    kind: SegmentKind
    text: str | None = None  # the host's script; None for a song
    track_id: str | None = None  # the song for "song" segments; the next song for "transition"
    audio_file: str | None = None  # file name inside the show's output folder
    duration_s: float | None = None


ShowStatus = Literal["pending", "generating", "ready", "failed"]


class Show(BaseModel):
    id: str
    created_at: datetime
    config: ShowConfig
    status: ShowStatus = "pending"
    error: str | None = None
    segments: list[Segment] = Field(default_factory=list)
    tracks: list[Track] = Field(default_factory=list)  # the tracks used, in order, for credits
    audio_file: str | None = None  # the assembled MP3 file name, set when status is "ready"
