"""Domain models.

These are the shapes of `catalog.json` (Track, Fact) and `show.json` (Show).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Energy = Literal["calm", "medium", "upbeat"]
ENERGIES: tuple[Energy, ...] = ("calm", "medium", "upbeat")  # in order, for distance


class Fact(BaseModel):
    text: str  # one plain sentence the host can say on air
    about: Literal["artist", "track"] = "track"
    source_url: str  # page the fact was read on
    quote: str | None = None  # the source text the fact is written from
    source_type: str | None = None  # for example "artist bio", "review"


class Track(BaseModel):
    id: str  # "<style>-<nn>"
    title: str
    artist: str
    style: str  # for example "jazz", "rock"
    energy: Energy | None = None  # None when unknown
    album: str
    year: int | None = None
    duration_s: float  # full track length in seconds
    license: str  # as the source page shows it, for example "CC BY-NC-SA 3.0 US"
    license_url: str
    track_url: str  # page where the track was found, used for credits
    artist_url: str
    audio_file: str  # file name inside catalog/audio/
    tags: list[str] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    spoken_title: str | None = None  # what the host says when the title is not speakable as is
    host_notes: str | None = None  # on-air guidance for the host, fed to the prompt
    # Curator notes. Excluded from every dump, so they never reach show.json, the API or
    # the replay page (D020).
    notes: str = Field("", exclude=True)

    @property
    def display_title(self) -> str:
        """The title to speak and to show: `spoken_title` when set, else `title`."""
        return self.spoken_title or self.title


class Persona(BaseModel):
    id: str  # for example "calm"
    name: str  # display name
    description: str  # one or two sentences fed to the prompt
    voice_id: str  # ElevenLabs voice id


MusicSourceName = Literal["catalog", "jamendo"]  # D029: the local catalog or Jamendo


class ShowConfig(BaseModel):
    style: str  # a style of the chosen source
    persona: str  # Persona.id
    language: Literal["en", "fr"] = "en"
    generator: Literal["pipeline"] = "pipeline"  # D008: only "pipeline" in Phase 1
    energy: Energy | None = None  # the listener's choice; None means any (D020)
    source: MusicSourceName = "catalog"  # where the songs come from (D029)
    wish: str | None = None  # free text that refines the search; Jamendo only (D030)
    track_count: int = 3
    demo: bool = False  # cut each song to a 30 s excerpt, for a fast short show
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
