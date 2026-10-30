"""MusicSource backed by `catalog/catalog.json` (D002).

The file is `{"version": 1, "tracks": [...]}` where each track follows the Track
model, and the audio files sit next to it in `catalog/audio/`.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from radio_llama.models import Track
from radio_llama.music.base import UnknownTrackError


class Catalog(BaseModel):
    """The file format of `catalog.json` (D014)."""

    version: Literal[1]
    tracks: list[Track]


class LocalCatalog:
    def __init__(self, catalog_path: Path, audio_dir: Path | None = None) -> None:
        self.catalog_path = catalog_path
        self.audio_dir = audio_dir if audio_dir is not None else catalog_path.parent / "audio"
        catalog = Catalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
        self._tracks = catalog.tracks
        self._by_id: dict[str, Track] = {}
        for track in self._tracks:
            if track.id in self._by_id:
                raise ValueError(f"Duplicate track id {track.id!r} in {catalog_path}")
            self._by_id[track.id] = track

    def list_styles(self) -> list[str]:
        return sorted({track.style for track in self._tracks})

    def list_tracks(self, style: str) -> list[Track]:
        return [track for track in self._tracks if track.style == style]

    def get_track(self, track_id: str) -> Track:
        try:
            return self._by_id[track_id]
        except KeyError:
            raise UnknownTrackError(f"No track with id {track_id!r}") from None

    def audio_path(self, track: Track) -> Path:
        """Where the track's audio file should be. The caller checks that it exists."""
        return self.audio_dir / track.audio_file
