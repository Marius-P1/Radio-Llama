"""Music sources: where the pipeline gets styles, tracks and audio files."""

from radio_llama.music.base import MusicSource, MusicSourceError, UnknownTrackError
from radio_llama.music.factory import SOURCE_NAMES, make_music_sources, missing_source_message
from radio_llama.music.jamendo import JamendoError, JamendoSource, WishFilters
from radio_llama.music.local_catalog import LocalCatalog

__all__ = [
    "SOURCE_NAMES",
    "JamendoError",
    "JamendoSource",
    "LocalCatalog",
    "MusicSource",
    "MusicSourceError",
    "UnknownTrackError",
    "WishFilters",
    "make_music_sources",
    "missing_source_message",
]
