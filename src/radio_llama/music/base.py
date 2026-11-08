"""The MusicSource protocol and its errors (D002, D014, D029)."""

from pathlib import Path
from typing import Protocol

from radio_llama.models import Track


class UnknownTrackError(LookupError):
    """No track has this id."""


class MusicSourceError(Exception):
    """A music source could not answer (network, quota, bad key). The message is readable."""


class MusicSource(Protocol):
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
        """Return the local audio file of `track`, fetching it first when the source is remote."""
        ...
