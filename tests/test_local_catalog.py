import json
from pathlib import Path

import pytest

from radio_llama.music import LocalCatalog, UnknownTrackError


def _track(track_id: str, style: str) -> dict:
    return {
        "id": track_id,
        "title": f"Title {track_id}",
        "artist": "Test Artist",
        "style": style,
        "album": "Test Album",
        "duration_s": 10.0,
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "track_url": f"https://example.com/{track_id}",
        "artist_url": "https://example.com/test-artist",
        "audio_file": f"{track_id}.mp3",
        "facts": [],
    }


def _write_catalog(path: Path, tracks: list[dict]) -> Path:
    path.write_text(json.dumps({"version": 1, "tracks": tracks}), encoding="utf-8")
    return path


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    tracks = [_track("j1", "jazz"), _track("r1", "rock"), _track("j2", "jazz")]
    return _write_catalog(tmp_path / "catalog.json", tracks)


def test_list_styles_is_sorted_and_unique(catalog_path: Path) -> None:
    assert LocalCatalog(catalog_path).list_styles() == ["jazz", "rock"]


def test_list_tracks_keeps_file_order(catalog_path: Path) -> None:
    tracks = LocalCatalog(catalog_path).list_tracks("jazz")
    assert [track.id for track in tracks] == ["j1", "j2"]


def test_list_tracks_unknown_style_is_empty(catalog_path: Path) -> None:
    assert LocalCatalog(catalog_path).list_tracks("polka") == []


def test_get_track_returns_the_track(catalog_path: Path) -> None:
    track = LocalCatalog(catalog_path).get_track("r1")
    assert track.id == "r1"
    assert track.style == "rock"


def test_get_track_unknown_id_raises(catalog_path: Path) -> None:
    with pytest.raises(UnknownTrackError):
        LocalCatalog(catalog_path).get_track("nope")


def test_duplicate_ids_raise_value_error(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path / "catalog.json", [_track("j1", "jazz"), _track("j1", "rock")])
    with pytest.raises(ValueError, match="j1"):
        LocalCatalog(path)


def test_audio_path_defaults_to_audio_folder_next_to_catalog(catalog_path: Path) -> None:
    catalog = LocalCatalog(catalog_path)
    track = catalog.get_track("j1")
    assert catalog.audio_path(track) == catalog_path.parent / "audio" / "j1.mp3"


def test_audio_path_uses_explicit_audio_dir(catalog_path: Path, tmp_path: Path) -> None:
    audio_dir = tmp_path / "elsewhere"
    catalog = LocalCatalog(catalog_path, audio_dir=audio_dir)
    track = catalog.get_track("r1")
    assert catalog.audio_path(track) == audio_dir / "r1.mp3"
