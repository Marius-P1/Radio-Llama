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
        duration_s=201.5,
        license="CC-BY-4.0",
        source_url="https://example.com/blue-morning",
        audio_file="t1.mp3",
        facts=[Fact(text="Recorded in 2019.", source_url="https://example.com/about")],
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


def test_show_config_rejects_agent_generator() -> None:
    with pytest.raises(ValidationError):
        ShowConfig(style="jazz", persona="calm", generator="agent")  # type: ignore[arg-type]
