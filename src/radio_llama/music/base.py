"""The MusicSource protocol and its errors (D002, D014, D029, D038)."""

from pathlib import Path
from typing import Literal, Protocol

from radio_llama.models import Track


class UnknownTrackError(LookupError):
    """No track has this id."""


class MusicSourceError(Exception):
    """A music source could not answer (network, quota, bad key). The message is readable."""


class MusicSource(Protocol):
    # How the songs reach the listener: "file" sources give an audio file per track that the
    # pipeline assembles into show.mp3; an "embed" source only names a video the player
    # streams itself, so the show keeps its speech files apart (D038).
    delivery: Literal["file", "embed"]
    # Whether a free-text wish refines the song choice on this source (D030).
    accepts_wish: bool
    # Whether the pipeline looks the artists up on MusicBrainz and Wikipedia (D036). The
    # catalog is hand-written with its own sourced facts; the online sources are not.
    needs_facts_lookup: bool
    # Whether the source can feed an endless show (D042): it must be able to give one more
    # song the show has not played, again and again. The local catalog has a dozen tracks
    # per style, so it keeps shows of a set length.
    endless: bool

    def list_styles(self) -> list[str]:
        """Return the styles the source offers, sorted and unique."""
        ...

    def list_tracks(self, style: str) -> list[Track]:
        """Return candidate tracks of `style`. Empty list for an unknown style."""
        ...

    def get_track(self, track_id: str) -> Track:
        """Return the track with this id. Raises UnknownTrackError if there is none."""
        ...

    def audio_path(self, track: Track) -> Path:
        """Return the local audio file of `track`, fetching it first when the source is remote.

        Raises MusicSourceError on an embed source, which has no audio file.
        """
        ...
