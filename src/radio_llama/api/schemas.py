"""Request and response bodies of the API. The frontend types are generated from these."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from radio_llama.models import Segment, Show, ShowConfig, ShowStatus, Track


class PersonaOption(BaseModel):
    id: str
    name: str
    description: str


class OptionsResponse(BaseModel):
    styles: list[str]
    personas: dict[str, list[PersonaOption]]  # keyed by language: {"en": [...], "fr": [...]}
    languages: list[str]  # ["en", "fr"]


class CreateShowRequest(BaseModel):
    style: str
    persona: str = "calm"
    language: Literal["en", "fr"] = "en"
    generator: Literal["pipeline"] = "pipeline"  # D008: only "pipeline" in Phase 1


class Progress(BaseModel):
    done: int
    total: int


class ShowSummary(BaseModel):
    id: str
    status: ShowStatus
    created_at: datetime
    config: ShowConfig
    error: str | None = None


class ShowResponse(ShowSummary):
    progress: Progress | None = None  # set while generating
    segments: list[Segment]
    tracks: list[Track]  # credits: artist, title, license, source_url are all in Track
    audio_url: str | None = None  # "/api/v1/shows/{id}/audio" once ready

    @classmethod
    def from_show(cls, show: Show, progress: Progress | None) -> "ShowResponse":
        audio_url = f"/api/v1/shows/{show.id}/audio" if show.status == "ready" else None
        return cls(
            id=show.id,
            status=show.status,
            created_at=show.created_at,
            config=show.config,
            error=show.error,
            progress=progress,
            segments=show.segments,
            tracks=show.tracks,
            audio_url=audio_url,
        )
