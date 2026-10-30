"""Checks on the real `catalog/catalog.json` shipped in the repo."""

import os
from pathlib import Path

import pytest

from radio_llama.music.local_catalog import Catalog

CATALOG_PATH = Path(__file__).resolve().parent.parent / "catalog" / "catalog.json"
ALLOWED_LICENSES = {"CC-BY-4.0", "CC-BY-SA-4.0", "CC-BY-3.0", "CC-BY-SA-3.0"}
EXPECTED_STYLES = ["electronic", "jazz", "lofi", "rock"]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def test_every_track_is_well_formed(catalog: Catalog) -> None:
    for track in catalog.tracks:
        assert track.license in ALLOWED_LICENSES, track.id
        assert track.source_url.startswith("https://"), track.id
        assert track.duration_s > 0, track.id
        assert track.audio_file.endswith(".mp3"), track.id
        assert track.facts, f"{track.id} has no facts"
        for fact in track.facts:
            assert fact.source_url.startswith("https://"), f"{track.id}: {fact.text!r}"


def test_ids_are_unique(catalog: Catalog) -> None:
    ids = [track.id for track in catalog.tracks]
    assert len(ids) == len(set(ids))


def test_audio_files_are_unique(catalog: Catalog) -> None:
    files = [track.audio_file for track in catalog.tracks]
    assert len(files) == len(set(files))


def test_styles_match_the_decision(catalog: Catalog) -> None:
    assert sorted({track.style for track in catalog.tracks}) == EXPECTED_STYLES


@pytest.mark.skipif(
    not os.environ.get("RELEASE_CHECK"), reason="set RELEASE_CHECK=1 before a public release"
)
def test_no_placeholder_track_remains(catalog: Catalog) -> None:
    placeholders = [track.id for track in catalog.tracks if track.placeholder]
    assert not placeholders, f"Placeholder tracks still in catalog.json: {placeholders}"
