"""Request and response bodies of the API. The frontend types are generated from these."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from radio_llama.models import (
    Energy,
    MusicSourceName,
    Segment,
    Show,
    ShowConfig,
    ShowStatus,
    Track,
)
from radio_llama.music.factory import delivery_of

WISH_MAX_CHARS = 200
# Voice ids are about twenty characters. The cap keeps a huge value away from the voice
# lookup and out of the "Unknown voice" message. FastAPI's own 422 reply still repeats
# the rejected value once, as it does for every field.
VOICE_ID_MAX_CHARS = 100


class PersonaOption(BaseModel):
    id: str
    name: str
    description: str
    voice_id: str  # the persona's own voice, used when the listener picks none (D040)


class SourceOption(BaseModel):
    id: MusicSourceName
    name: str  # "Local catalog", "Jamendo", "YouTube"
    styles: list[str]  # the styles this source offers
    wish: bool  # whether a free-text wish refines the song choice on this source (D030)


class OptionsResponse(BaseModel):
    sources: list[SourceOption]  # the configured music sources, the local catalog first
    personas: dict[str, list[PersonaOption]]  # keyed by language: {"en": [...], "fr": [...]}
    languages: list[str]  # ["en", "fr"]
    energies: list[str]  # ["calm", "medium", "upbeat"]; a request may also leave energy out


class CreateShowRequest(BaseModel):
    # Required on a source that offers styles, absent or null on one that does not (D039).
    style: str | None = None
    persona: str = "calm"
    # A voice id from GET /voices. None or blank: the persona's own voice (D040).
    voice_id: str | None = Field(default=None, max_length=VOICE_ID_MAX_CHARS)
    language: Literal["en", "fr"] = "en"
    generator: Literal["pipeline"] = "pipeline"  # D008: only "pipeline" in Phase 1
    energy: Energy | None = None  # None: any energy (D020)
    source: MusicSourceName = "catalog"  # D029
    wish: str | None = Field(default=None, max_length=WISH_MAX_CHARS)  # Jamendo, YouTube (D030)
    demo: bool = False  # True: 30 s excerpts instead of full songs (D023)


class Progress(BaseModel):
    done: int
    total: int


class ShowSummary(BaseModel):
    id: str
    status: ShowStatus
    created_at: datetime
    config: ShowConfig
    error: str | None = None


class SegmentResponse(Segment):
    # "/api/v1/shows/{id}/segments/{index}/audio" for a speech segment of a ready show,
    # None for a song and before the show is ready (D038).
    audio_url: str | None = None


class ShowResponse(ShowSummary):
    progress: Progress | None = None  # set while generating
    # How the show plays once ready (D038), known from its source at any status. "file":
    # one MP3 at audio_url. "segments": the player runs the segments in order, speech from
    # each segment's audio_url, songs in the embedded YouTube player by the track's
    # video_id for duration_s seconds.
    playback: Literal["file", "segments"]
    segments: list[SegmentResponse]
    tracks: list[Track]  # credits: artist, title, license and the links are all in Track
    audio_url: str | None = None  # "/api/v1/shows/{id}/audio" once ready, None for segments

    @classmethod
    def from_show(cls, show: Show, progress: Progress | None) -> "ShowResponse":
        ready = show.status == "ready"
        playback = "segments" if delivery_of(show.config.source) == "embed" else "file"
        return cls(
            id=show.id,
            status=show.status,
            created_at=show.created_at,
            config=show.config,
            error=show.error,
            progress=progress,
            playback=playback,
            segments=[
                SegmentResponse(
                    **segment.model_dump(),
                    audio_url=(
                        f"/api/v1/shows/{show.id}/segments/{index}/audio"
                        if ready and segment.kind != "song" and segment.audio_file is not None
                        else None
                    ),
                )
                for index, segment in enumerate(show.segments)
            ],
            tracks=show.tracks,
            audio_url=f"/api/v1/shows/{show.id}/audio" if ready and playback == "file" else None,
        )
