"""The MusicSource protocol and its errors (D002, D014)."""

from typing import Protocol

from radio_llama.models import Track


class UnknownTrackError(LookupError):
    """No track has this id."""


class MusicSource(Protocol):
    def list_styles(self) -> list[str]:
        """Return the styles that have at least one track, sorted and unique."""
        ...

    def list_tracks(self, style: str) -> list[Track]:
        """Return the tracks of `style` in catalog order. Empty list for an unknown style."""
        ...

    def get_track(self, track_id: str) -> Track:
        """Return the track with this id. Raises UnknownTrackError if there is none."""
        ...
