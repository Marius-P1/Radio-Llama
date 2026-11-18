from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from radio_llama.models import Fact, Segment, Show, ShowConfig, Track


def test_show_round_trips_through_json() -> None:
    track = Track(
        id="t1",
        title="Blue Morning",
        artist="Some Artist",
        style="jazz",
        energy="calm",
        album="Early Light",
        year=2021,
        duration_s=201.5,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        track_url="https://example.com/blue-morning",
        artist_url="https://example.com/some-artist",
        audio_file="t1.mp3",
        tags=["piano", "mellow"],
        facts=[
            Fact(
                text="Recorded in 2019.",
                about="track",
                source_url="https://example.com/about",
                quote="We recorded it in 2019.",
                source_type="artist bio",
            )
        ],
    )
    show = Show(
        id="show-1",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm"),
        status="ready",
        segments=[
            Segment(kind="intro", text="Welcome.", audio_file="00-intro.mp3", duration_s=4.2),
            Segment(kind="song", track_id="t1", duration_s=30.0),
            Segment(kind="outro", text="Goodbye.", audio_file="02-outro.mp3", duration_s=3.1),
        ],
        tracks=[track],
        audio_file="show.mp3",
    )

    restored = Show.model_validate_json(show.model_dump_json())

    assert restored == show


def test_a_show_file_written_before_the_voice_picker_still_loads() -> None:
    """Old show.json files have no voice fields: None means the persona's own voice (D040)."""
    config = ShowConfig.model_validate_json('{"style": "jazz", "persona": "calm"}')

    assert config.voice_id is None
    assert config.voice_name is None
    picked = ShowConfig(style="jazz", persona="calm", voice_id="abc", voice_name="Gabriel")
    assert ShowConfig.model_validate_json(picked.model_dump_json()) == picked


def test_a_show_file_written_before_the_endless_show_still_loads() -> None:
    """Old show.json files have no `endless` and no `end_note` (D044)."""
    show = Show.model_validate_json(
        '{"id": "show-1", "created_at": "2026-10-30T12:00:00Z", "status": "ready", '
        '"config": {"style": "jazz", "persona": "calm"}, "audio_file": "show.mp3"}'
    )

    assert show.config.endless is False
    assert show.end_note is None
    assert show.is_open is False
    closed = show.model_copy(update={"end_note": "The show ended by itself."})
    assert Show.model_validate_json(closed.model_dump_json()) == closed


def test_an_endless_show_is_open_until_its_outro() -> None:
    """No status says it: an open show is an endless one whose last segment is no outro."""

    def show(endless: bool, kinds: list[str]) -> Show:
        return Show(
            id="show-1",
            created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
            config=ShowConfig(style="jazz", persona="calm", source="jamendo", endless=endless),
            status="ready",
            segments=[Segment(kind=kind) for kind in kinds],  # type: ignore[arg-type]
        )

    assert show(True, ["intro", "song"]).is_open is True
    assert show(True, ["intro", "song", "transition", "song"]).is_open is True
    assert show(True, ["intro", "song", "outro"]).is_open is False
    assert show(True, []).is_open is True  # not started yet
    # A show of a set length is never open, whatever it holds.
    assert show(False, ["intro", "song"]).is_open is False
    assert show(False, ["intro", "song", "outro"]).is_open is False
    # A derived value, not a field: it never reaches show.json.
    assert "is_open" not in show(True, ["intro", "song"]).model_dump()


def test_track_notes_never_leave_the_model() -> None:
    track = Track(
        id="t1",
        title="Blue Morning (ID 12)",
        artist="Some Artist",
        style="jazz",
        album="Early Light",
        duration_s=201.5,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        track_url="https://example.com/blue-morning",
        artist_url="https://example.com/some-artist",
        audio_file="t1.mp3",
        spoken_title="Blue Morning",
        notes="Curator only.",
    )

    assert track.notes == "Curator only."
    assert "notes" not in track.model_dump()
    assert "Curator only" not in track.model_dump_json()
    assert track.display_title == "Blue Morning"
    assert track.model_copy(update={"spoken_title": None}).display_title == "Blue Morning (ID 12)"


def test_show_config_rejects_agent_generator() -> None:
    with pytest.raises(ValidationError):
        ShowConfig(style="jazz", persona="calm", generator="agent")  # type: ignore[arg-type]
