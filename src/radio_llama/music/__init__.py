"""Music sources: where the pipeline gets styles, tracks and audio files."""

from radio_llama.music.base import MusicSource, UnknownTrackError
from radio_llama.music.local_catalog import LocalCatalog

__all__ = ["LocalCatalog", "MusicSource", "UnknownTrackError"]
