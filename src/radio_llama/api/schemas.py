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
from radio_llama.music.factory import playback_of

WISH_MAX_CHARS = 200
# Voice ids are about twenty characters. The cap keeps a huge value away from the voice
# lookup and out of the "Unknown voice" message. FastAPI's own 422 reply still repeats
# the rejected value once, as it does for every field.
VOICE_ID_MAX_CHARS = 100
# How many songs a show made through the API may have (D041). The smallest style of the
# real catalog has 11 tracks, so a catalog show of ten songs can always be filled. The
# bound also keeps the wait reasonable, and a YouTube show's searches: one per song,
# 100 a day. The CLI's --tracks has no such bound.
TRACK_COUNT_MIN = 1
TRACK_COUNT_MAX = 10


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
    endless: bool  # whether this source can feed an endless show (D042)


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
    # The number of songs (D041). A count the source cannot fill gives a failed show.
    track_count: int = Field(default=3, ge=TRACK_COUNT_MIN, le=TRACK_COUNT_MAX)
    demo: bool = False  # True: 30 s excerpts instead of full songs (D023)
    # True: a show with no set length, on a source whose `endless` is true (D042). It is
    # ready once its first song is, then grows through POST /shows/{id}/next. The
    # track_count of the request is ignored.
    endless: bool = False


class NextPartRequest(BaseModel):
    """Body of POST /shows/{id}/next (D044)."""

    # How many segments of the show the player has. The next part is made only when the
    # show has no more than that, so asking twice for the same part makes it once.
    known: int = Field(ge=0)


class EndShowRequest(BaseModel):
    """Body of POST /shows/{id}/end (D044)."""

    # The index of the song segment after which the show ends: the song that is playing,
    # or the next one when the host is speaking. Segments after it are dropped.
    after: int = Field(ge=0)


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
    # "/api/v1/shows/{id}/segments/{index}/audio" once the show is ready, for a speech
    # segment (D038) and for a song that has its own file, which only the songs of an
    # endless Jamendo show do (D044). None for every other song: it is inside show.mp3,
    # or it plays in the embedded YouTube player.
    audio_url: str | None = None


class ShowResponse(ShowSummary):
    progress: Progress | None = None  # set while generating
    # How the show plays once ready, known from its config at any status (D038, D044).
    # "file": one MP3 at audio_url. "segments": the player runs the segments in order. A
    # segment with an audio_url plays from it, speech or song. A song without one plays in
    # the embedded YouTube player by the track's video_id for duration_s seconds.
    playback: Literal["file", "segments"]
    segments: list[SegmentResponse]
    tracks: list[Track]  # credits: artist, title, license and the links are all in Track
    audio_url: str | None = None  # "/api/v1/shows/{id}/audio" once ready, None for segments
    # Why an endless show closed by itself, as a sentence for the listener. None when the
    # listener ended it, while it is open, and on every other show (D044).
    end_note: str | None = None

    @classmethod
    def from_show(cls, show: Show, progress: Progress | None) -> "ShowResponse":
        ready = show.status == "ready"
        playback = playback_of(show.config)
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
                        if ready and has_own_audio(segment, playback)
                        else None
                    ),
                )
                for index, segment in enumerate(show.segments)
            ],
            tracks=show.tracks,
            audio_url=f"/api/v1/shows/{show.id}/audio" if ready and playback == "file" else None,
            end_note=show.end_note,
        )


def has_own_audio(segment: Segment, playback: Literal["file", "segments"]) -> bool:
    """Whether the segment audio route serves this segment's file.

    Speech always, when it has a file. A song only in a show that plays by segments: the
    songs of a file show have files too, but they are the pieces of show.mp3 and are
    never served on their own (D038, D044).
    """
    if segment.audio_file is None:
        return False
    return segment.kind != "song" or playback == "segments"
