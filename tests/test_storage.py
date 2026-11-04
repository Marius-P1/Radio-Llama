import json
import re
from datetime import UTC, datetime
from pathlib import Path

from radio_llama.models import Show, ShowConfig, Track
from radio_llama.storage import SHOW_FILE, load_show, new_show_id, save_show


def _show() -> Show:
    return Show(
        id="test-show",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm"),
    )


def test_save_show_creates_folder_and_round_trips(tmp_path: Path) -> None:
    out_dir = tmp_path / "output" / "test-show"
    show = _show()

    path = save_show(show, out_dir)

    assert path == out_dir / SHOW_FILE
    assert path.read_text(encoding="utf-8").endswith("}\n")
    assert load_show(out_dir) == show


def test_show_json_carries_no_curator_notes(tmp_path: Path) -> None:
    show = _show()
    show.tracks = [
        Track(
            id="t1",
            title="Blue Morning",
            artist="Some Artist",
            style="jazz",
            album="Early Light",
            duration_s=201.5,
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            track_url="https://example.com/blue-morning",
            artist_url="https://example.com/some-artist",
            audio_file="t1.mp3",
            notes="Curator only.",
        )
    ]

    path = save_show(show, tmp_path)

    text = path.read_text(encoding="utf-8")
    assert '"notes"' not in text
    assert "Curator only" not in text
    assert load_show(tmp_path).tracks[0].notes == ""


def test_load_show_accepts_the_old_song_length_field(tmp_path: Path) -> None:
    """Shows written before D023 carry `song_length_s` and no `demo`. They still load."""
    old = {
        "id": "20261103-170852-00546e",
        "created_at": "2026-11-03T17:08:52Z",
        "config": {
            "style": "electronic",
            "persona": "energetic",
            "language": "en",
            "generator": "pipeline",
            "energy": None,
            "track_count": 3,
            "song_length_s": 30.0,
            "seed": None,
        },
        "status": "ready",
        "error": None,
        "segments": [
            {"kind": "intro", "text": "Hello.", "audio_file": "00-intro.mp3", "duration_s": 4.1},
            {
                "kind": "song",
                "track_id": "electronic-01",
                "audio_file": "01-song.mp3",
                "duration_s": 30.0,
            },
            {"kind": "outro", "text": "Bye.", "audio_file": "02-outro.mp3", "duration_s": 3.2},
        ],
        "tracks": [],
        "audio_file": "show.mp3",
    }
    (tmp_path / SHOW_FILE).write_text(json.dumps(old), encoding="utf-8")

    show = load_show(tmp_path)

    assert show.config.demo is False
    assert "song_length_s" not in show.config.model_dump()
    assert show.segments[1].duration_s == 30.0


def test_new_show_id_has_timestamp_and_suffix() -> None:
    show_id = new_show_id(datetime(2026, 10, 30, 18, 15, 30, tzinfo=UTC))
    assert re.fullmatch(r"20261030-181530-[0-9a-f]{6}", show_id)


def test_new_show_id_differs_between_calls() -> None:
    now = datetime(2026, 10, 30, 18, 15, 30, tzinfo=UTC)
    assert new_show_id(now) != new_show_id(now)
