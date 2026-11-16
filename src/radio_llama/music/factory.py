"""Build the music sources the settings allow, keyed by the name a show request uses (D029)."""

from radio_llama.config import Settings
from radio_llama.music.base import MusicSource
from radio_llama.music.jamendo import JamendoSource
from radio_llama.music.local_catalog import LocalCatalog
from radio_llama.music.youtube import YouTubeSource

SOURCE_NAMES = {"catalog": "Local catalog", "jamendo": "Jamendo", "youtube": "YouTube"}


def make_music_sources(settings: Settings) -> dict[str, MusicSource]:
    """The local catalog always; Jamendo and YouTube when their key is set."""
    sources: dict[str, MusicSource] = {"catalog": LocalCatalog(settings.catalog_path)}
    if settings.jamendo_client_id:
        sources["jamendo"] = JamendoSource(settings.jamendo_client_id, settings.cache_dir)
    if settings.youtube_api_key is not None:
        sources["youtube"] = YouTubeSource(
            settings.require_api_key("youtube"), settings.cache_dir, settings.youtube_region
        )
    return sources


def missing_source_message(name: str) -> str:
    if name == "jamendo":
        return "Music source 'jamendo' is not configured. Set JAMENDO_CLIENT_ID in .env."
    if name == "youtube":
        return "Music source 'youtube' is not configured. Set YOUTUBE_API_KEY in .env."
    return f"Music source {name!r} is not configured."
