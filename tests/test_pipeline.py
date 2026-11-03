import random
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radio_llama.config import Settings
from radio_llama.generator import GenerationError, PipelineGenerator, run_generation
from radio_llama.generator.pipeline import pick_tracks
from radio_llama.llm import FakeLLM
from radio_llama.models import Show, ShowConfig, Track
from radio_llama.music import LocalCatalog
from radio_llama.storage import load_show
from radio_llama.voice import CachedTextToSpeech, FakeTTS, TextToSpeech, TTSError, Voice

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CANNED = "This is a fake radio script."
EXPECTED_KINDS = ["intro", "song", "transition", "song", "transition", "song", "outro"]


class FailingTTS:
    def synthesize(self, text: str, voice: Voice) -> Path:
        raise TTSError("Fake TTS: the voice is out today")


def _show(**config) -> Show:
    return Show(
        id="test-show",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", song_length_s=1.5, **config),
    )


def _generator(
    settings: Settings, catalog: LocalCatalog, tts: TextToSpeech | None = None
) -> PipelineGenerator:
    if tts is None:
        tts = CachedTextToSpeech(FakeTTS(settings.cache_dir / "tts-raw"), settings.cache_dir)
    return PipelineGenerator(FakeLLM([CANNED]), tts, catalog, settings)


def test_run_generation_builds_a_ready_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    generator = _generator(fake_settings, tiny_catalog)
    progress: list[tuple[int, int]] = []
    out_dir = tmp_path / "output" / "test-show"

    def on_progress(done: int, total: int) -> None:
        progress.append((done, total))

    show = run_generation(generator, _show(seed=1), out_dir, on_progress)

    assert show.status == "ready"
    assert show.error is None
    assert [segment.kind for segment in show.segments] == EXPECTED_KINDS
    assert (out_dir / "show.mp3").exists()
    assert (out_dir / "show.json").exists()
    # Dumps, not models: the catalog's curator notes stay in memory and never reach show.json.
    assert load_show(out_dir).model_dump() == show.model_dump()
    for segment in show.segments:
        assert segment.audio_file is not None
        assert segment.duration_s is not None and segment.duration_s > 0
        if segment.kind == "song":
            assert segment.text is None
        else:
            assert segment.text == CANNED
    assert len(show.tracks) == 3
    assert all(track.style == "jazz" for track in show.tracks)
    assert [segment.track_id for segment in show.segments if segment.kind == "song"] == [
        track.id for track in show.tracks
    ]
    assert progress == [(done, 7) for done in range(1, 8)]
    assert len(generator.last_responses) == 4


def test_same_seed_picks_the_same_tracks(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    first = run_generation(_generator(fake_settings, tiny_catalog), _show(seed=7), tmp_path / "a")
    second = run_generation(_generator(fake_settings, tiny_catalog), _show(seed=7), tmp_path / "b")

    assert [t.id for t in first.tracks] == [t.id for t in second.tracks]


def test_too_few_tracks_fails_with_a_message(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"

    show = run_generation(_generator(fake_settings, tiny_catalog), _show(track_count=99), out_dir)

    assert show.status == "failed"
    assert show.error is not None
    assert "99 needed" in show.error
    assert load_show(out_dir).status == "failed"


def test_generation_error_is_raised_by_the_generator(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    with pytest.raises(GenerationError, match="needed"):
        _generator(fake_settings, tiny_catalog).generate(_show(track_count=99), tmp_path / "out")


def test_tts_error_marks_the_show_failed(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    generator = _generator(fake_settings, tiny_catalog, tts=FailingTTS())

    show = run_generation(generator, _show(), tmp_path / "out")

    assert show.status == "failed"
    assert show.error is not None
    assert "voice is out" in show.error


def _track(track_id: str, energy: str | None) -> Track:
    return Track(
        id=track_id,
        title=track_id,
        artist="Artist",
        style="jazz",
        energy=energy,  # type: ignore[arg-type]
        album="Album",
        duration_s=100.0,
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        track_url="https://example.com",
        artist_url="https://example.com",
        audio_file=f"{track_id}.mp3",
    )


CANDIDATES = [
    _track("calm-1", "calm"),
    _track("calm-2", "calm"),
    _track("medium-1", "medium"),
    _track("medium-2", "medium"),
    _track("upbeat-1", "upbeat"),
    _track("upbeat-2", "upbeat"),
    _track("unknown-1", None),
    _track("unknown-2", None),
]


def _energies(tracks: list[Track]) -> list[str | None]:
    return [track.energy for track in tracks]


def test_pick_without_energy_samples_everything() -> None:
    picked = pick_tracks(CANDIDATES, 3, None, random.Random(1))

    assert len(picked) == 3
    assert len({track.id for track in picked}) == 3
    assert pick_tracks(CANDIDATES, 3, None, random.Random(1)) == picked
    assert pick_tracks(CANDIDATES, 8, None, random.Random(1)) != CANDIDATES


def test_pick_takes_the_requested_energy_first() -> None:
    picked = pick_tracks(CANDIDATES, 2, "calm", random.Random(1))

    assert _energies(picked) == ["calm", "calm"]


def test_pick_fills_with_unknown_then_nearest_energies() -> None:
    picked = pick_tracks(CANDIDATES, 7, "calm", random.Random(1))

    assert _energies(picked) == ["calm", "calm", None, None, "medium", "medium", "upbeat"]
    assert len({track.id for track in picked}) == 7


def test_pick_from_upbeat_goes_through_medium_before_calm() -> None:
    picked = pick_tracks(CANDIDATES, 8, "upbeat", random.Random(3))

    expected = ["upbeat", "upbeat", None, None, "medium", "medium", "calm", "calm"]
    assert _energies(picked) == expected


def test_pick_from_medium_treats_calm_and_upbeat_alike() -> None:
    picked = pick_tracks(CANDIDATES, 8, "medium", random.Random(5))

    assert _energies(picked[:4]) == ["medium", "medium", None, None]
    assert sorted(str(energy) for energy in _energies(picked[4:])) == [
        "calm",
        "calm",
        "upbeat",
        "upbeat",
    ]


def test_pick_is_reproducible_with_a_seed() -> None:
    first = pick_tracks(CANDIDATES, 5, "upbeat", random.Random(42))
    second = pick_tracks(CANDIDATES, 5, "upbeat", random.Random(42))
    assert first == second


def test_pick_samples_inside_a_tier_larger_than_count() -> None:
    picks = {pick_tracks(CANDIDATES, 1, "calm", random.Random(seed))[0].id for seed in range(20)}
    assert picks == {"calm-1", "calm-2"}


def test_show_with_energy_reaches_ready(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(
        _generator(fake_settings, tiny_catalog), _show(energy="upbeat", seed=1), tmp_path
    )

    assert show.status == "ready"
    assert show.config.energy == "upbeat"
    assert len(show.tracks) == 3


def test_french_show_reaches_ready(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(_generator(fake_settings, tiny_catalog), _show(language="fr"), tmp_path)

    assert show.status == "ready"
