"""Shared fixtures: a tiny catalog with real (tone) audio and settings that use the fakes."""

import json
import subprocess
from pathlib import Path

import pytest

from radio_llama.config import Settings
from radio_llama.music import LocalCatalog

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
TONE_SECONDS = 3.0


def _track(track_id: str, style: str, number: int) -> dict:
    return {
        "id": track_id,
        "title": f"Title {track_id}",
        "artist": f"Test Artist {number}",
        "style": style,
        "album": "Test Album",
        "year": 2020,
        "duration_s": TONE_SECONDS,
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "track_url": f"https://example.com/{track_id}",
        "artist_url": f"https://example.com/artist-{number}",
        "audio_file": f"{track_id}.mp3",
        "tags": ["test"],
        "facts": [{"text": f"Fact about {track_id}.", "source_url": "https://example.com"}],
        "notes": "Curator notes that must never leave the catalog.",
    }


def _tone(path: Path, frequency: float, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(path),
        ],
        check=True,
    )


@pytest.fixture
def tiny_catalog(tmp_path: Path) -> LocalCatalog:
    """Two styles (jazz, rock), three tracks each, with a 3 s tone per track."""
    tracks = [
        _track(f"{style}-{number}", style, number)
        for style in ("jazz", "rock")
        for number in (1, 2, 3)
    ]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"version": 1, "tracks": tracks}), encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    for index, track in enumerate(tracks):
        _tone(audio_dir / track["audio_file"], 220.0 * (index + 1), TONE_SECONDS)
    return LocalCatalog(catalog_path)


@pytest.fixture
def fake_settings(tmp_path: Path, tiny_catalog: LocalCatalog) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="fake",
        tts_backend="fake",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        catalog_path=tiny_catalog.catalog_path,
        prompts_dir=PROMPTS_DIR,
    )
