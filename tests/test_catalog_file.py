"""Checks on the real `catalog/catalog.json` shipped in the repo (D020).

The audio files are not required to exist: they come from the release asset.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

from radio_llama.music.local_catalog import Catalog

CATALOG_PATH = Path(__file__).resolve().parent.parent / "catalog" / "catalog.json"

# Every license string the catalog may use, written as the source page shows it, with the
# creativecommons.org page it must link to. Any CC license that allows redistribution with
# attribution is fine, NC included (non-commercial demo); ND is excluded.
ALLOWED_LICENSES = {
    "CC BY 3.0": "https://creativecommons.org/licenses/by/3.0/",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC BY-SA 3.0": "https://creativecommons.org/licenses/by-sa/3.0/",
    "CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC BY-NC 3.0": "https://creativecommons.org/licenses/by-nc/3.0/",
    "CC BY-NC 4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC BY-NC-SA 3.0": "https://creativecommons.org/licenses/by-nc-sa/3.0/",
    "CC BY-NC-SA 3.0 US": "https://creativecommons.org/licenses/by-nc-sa/3.0/us/",
    "CC BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
}
EXPECTED_STYLES = {"electronic", "jazz", "lofi", "rock"}
MIN_TRACKS_PER_STYLE = 3
# Artist-Title.mp3 or Artist-Title-Qualifier.mp3, ASCII letters and digits only (D020).
AUDIO_FILE = re.compile(r"^[A-Za-z0-9]+-[A-Za-z0-9]+(-[A-Za-z0-9]+)?\.mp3$")


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def test_every_track_is_well_formed(catalog: Catalog) -> None:
    for track in catalog.tracks:
        assert track.license in ALLOWED_LICENSES, track.id
        assert track.license_url == ALLOWED_LICENSES[track.license], track.id
        for url in (track.track_url, track.artist_url, track.license_url):
            assert url.startswith("https://"), f"{track.id}: {url}"
        assert track.duration_s > 0, track.id
        assert AUDIO_FILE.match(track.audio_file), f"{track.id}: {track.audio_file}"
        assert track.album, track.id


def test_every_track_has_two_or_three_sourced_facts(catalog: Catalog) -> None:
    for track in catalog.tracks:
        assert 2 <= len(track.facts) <= 3, f"{track.id} has {len(track.facts)} facts"
        for fact in track.facts:
            assert fact.source_url.startswith("https://"), f"{track.id}: {fact.text!r}"
            assert fact.quote, f"{track.id}: {fact.text!r} has no quote"


def test_ids_are_unique(catalog: Catalog) -> None:
    ids = [track.id for track in catalog.tracks]
    assert len(ids) == len(set(ids))


def test_audio_files_are_unique(catalog: Catalog) -> None:
    files = [track.audio_file for track in catalog.tracks]
    assert len(files) == len(set(files))


def test_styles_match_the_decision(catalog: Catalog) -> None:
    per_style = Counter(track.style for track in catalog.tracks)
    assert set(per_style) == EXPECTED_STYLES
    for style, count in per_style.items():
        assert count >= MIN_TRACKS_PER_STYLE, f"{style} has only {count} tracks"
