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
