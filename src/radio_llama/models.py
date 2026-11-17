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
    # File name inside catalog/audio/ or the source's cache. None for a YouTube track, which
    # the player streams through the embedded player instead (D038).
    audio_file: str | None = None
    video_id: str | None = None  # the YouTube video, set only by the YouTube source
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


MusicSourceName = Literal["catalog", "jamendo", "youtube"]  # D029, D038


class ShowConfig(BaseModel):
    # A style of the chosen source. None on YouTube, which offers no styles: the wish is
    # the theme there (D039).
    style: str | None = None
    persona: str  # Persona.id: how the host talks
    # Who the host sounds like (D040). None: the persona's own voice. The name is stored
    # with the id so the player can show it without asking the TTS backend.
    voice_id: str | None = None
    voice_name: str | None = None
    language: Literal["en", "fr"] = "en"
    generator: Literal["pipeline"] = "pipeline"  # D008: only "pipeline" in Phase 1
    energy: Energy | None = None  # the listener's choice; None means any (D020)
    source: MusicSourceName = "catalog"  # where the songs come from (D029)
    wish: str | None = None  # free text that refines the song choice; Jamendo and YouTube (D030)
    # The number of songs. On an endless show it is not a target: the generator keeps it
    # equal to the songs the show has so far, so a label that reads it stays true (D044).
    track_count: int = 3
    demo: bool = False  # cut each song to a 30 s excerpt, for a fast short show
    # A show with no set length (D042): it opens with one song and grows one break and one
    # song at a time while someone listens. Jamendo and YouTube only.
    endless: bool = False
    seed: int | None = None  # fixes the random track pick, for reproducible runs (D017)


SegmentKind = Literal["intro", "song", "transition", "outro"]


class Segment(BaseModel):
    kind: SegmentKind
    text: str | None = None  # the host's script; None for a song
    track_id: str | None = None  # the song for "song" segments; the next song for "transition"
    shape: str | None = None  # the break shape of a "transition" (D033), from shapes.json
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
    # The assembled MP3 file name, set when status is "ready". None on a ready YouTube show:
    # its speech segments have files and its songs play in the embedded player (D038). None
    # on an endless show too, which is never joined: every segment has its own file (D044).
    audio_file: str | None = None
    # Why an endless show closed by itself: the source had no more songs, YouTube's quota
    # ran out, or the show reached ENDLESS_MAX_SONGS. A sentence for the listener. None
    # when the listener ended it, and on every other show (D044).
    end_note: str | None = None

    @property
    def is_open(self) -> bool:
        """Whether this is an endless show the host has not closed yet (D044).

        There is no status for it: an open show is an endless show whose last segment is
        not an outro. A show the listener walked away from stays open for good, and
        nobody has to tell the server.
        """
        closed = bool(self.segments) and self.segments[-1].kind == "outro"
        return self.config.endless and not closed
