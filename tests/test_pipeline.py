import json
import random
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radio_llama.audio import AudioError, assemble
from radio_llama.config import Settings
from radio_llama.generator import GenerationError, PipelineGenerator, pipeline, run_generation
from radio_llama.generator.pipeline import (
    END_NOTE_NO_SONGS,
    END_NOTE_QUOTA,
    MAX_TRACK_FACTS,
    merge_facts,
    pick_tracks,
)
from radio_llama.llm import FakeLLM, LLMError
from radio_llama.models import Fact, Show, ShowConfig, Track
from radio_llama.music import JamendoError, LocalCatalog
from radio_llama.personas import get_persona
from radio_llama.script import plan_shapes, writer
from radio_llama.storage import load_show, save_show
from radio_llama.voice import CachedTextToSpeech, FakeTTS, TextToSpeech, TTSError, Voice
from tests.conftest import TONE_SECONDS, FakeFactServices, FakeJamendo, FakeYouTube

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CANNED = "This is a fake radio script."
EXPECTED_KINDS = ["intro", "song", "transition", "song", "transition", "song", "outro"]


class FailingTTS:
    def synthesize(self, text: str, voice: Voice) -> Path:
        raise TTSError("Fake TTS: the voice is out today")


def _show(**config) -> Show:
    """A jazz show on the catalog with the calm host; `config` overrides any field."""
    return Show(
        id="test-show",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(**{"style": "jazz", "persona": "calm", **config}),
    )


def _generator(
    settings: Settings, catalog: LocalCatalog, tts: TextToSpeech | None = None
) -> PipelineGenerator:
    if tts is None:
        tts = CachedTextToSpeech(FakeTTS(settings.cache_dir / "tts-raw"), settings.cache_dir)
    return PipelineGenerator(FakeLLM([CANNED]), tts, {"catalog": catalog}, settings)


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
    breaks = [segment for segment in show.segments if segment.kind == "transition"]
    assert all(segment.shape for segment in breaks)
    assert breaks[0].shape != breaks[1].shape
    assert all(segment.shape is None for segment in show.segments if segment.kind != "transition")
    # The total is known before the facts lookups and the first LLM call (D038).
    assert progress == [(done, 7) for done in range(8)]
    assert len(generator.last_responses) == 4


def test_same_seed_picks_the_same_breaks(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    first = run_generation(_generator(fake_settings, tiny_catalog), _show(seed=3), tmp_path / "a")
    second = run_generation(_generator(fake_settings, tiny_catalog), _show(seed=3), tmp_path / "b")

    assert [s.shape for s in first.segments] == [s.shape for s in second.segments]


def test_write_script_writes_text_without_audio(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    generator = _generator(fake_settings, tiny_catalog)

    show = generator.write_script(_show(seed=1))

    assert [segment.kind for segment in show.segments] == EXPECTED_KINDS
    assert all(segment.text == CANNED for segment in show.segments if segment.kind != "song")
    assert all(segment.audio_file is None for segment in show.segments)
    assert show.audio_file is None
    assert len(show.tracks) == 3
    assert len(generator.last_responses) == 4
    # Only the trace is written (D037): no show folder, no audio.
    assert [path.name for path in (tmp_path / "output").iterdir()] == ["scripts"]
    assert not (tmp_path / "output" / show.id).exists()


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


class RecordingTTS:
    """A fake TTS that remembers the voice of every call."""

    def __init__(self, work_dir: Path) -> None:
        self._inner = FakeTTS(work_dir)
        self.voices: list[Voice] = []

    def synthesize(self, text: str, voice: Voice) -> Path:
        self.voices.append(voice)
        return self._inner.synthesize(text, voice)


def test_the_picked_voice_reaches_the_tts(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    tts = RecordingTTS(tmp_path / "raw")
    generator = _generator(fake_settings, tiny_catalog, tts=tts)

    show = run_generation(generator, _show(voice_id="picked-voice"), tmp_path / "out")

    assert show.status == "ready", show.error
    assert len(tts.voices) == 4
    assert {voice.voice_id for voice in tts.voices} == {"picked-voice"}
    assert {voice.model_id for voice in tts.voices} == {fake_settings.elevenlabs_model_id}


def test_without_a_picked_voice_the_persona_speaks_with_its_own(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    tts = RecordingTTS(tmp_path / "raw")
    generator = _generator(fake_settings, tiny_catalog, tts=tts)

    show = run_generation(generator, _show(persona="witty"), tmp_path / "out")

    assert show.status == "ready", show.error
    witty = get_persona(fake_settings.prompts_dir, "en", "witty")
    assert {voice.voice_id for voice in tts.voices} == {witty.voice_id}


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


# Jamendo shows (D030): the same pipeline on a remote source, through the fake transport.

FILTERS_JSON = '{"tags": ["piano", "rainy"], "vocal": "instrumental", "lang": null, "search": null}'


def _jamendo_generator(
    settings: Settings,
    catalog: LocalCatalog,
    jamendo: FakeJamendo,
    llm: FakeLLM,
    facts: FakeFactServices | None = None,
) -> PipelineGenerator:
    tts = CachedTextToSpeech(FakeTTS(settings.cache_dir / "tts-raw"), settings.cache_dir)
    sources = {"catalog": catalog, "jamendo": jamendo.source}
    lookup = facts.lookup if facts is not None else None
    return PipelineGenerator(llm, tts, sources, settings, facts=lookup)


def test_jamendo_show_reaches_ready_with_cached_audio_and_facts(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))

    show = run_generation(generator, _show(source="jamendo", seed=1), tmp_path / "out")

    assert show.status == "ready", show.error
    assert show.config.source == "jamendo"
    assert [segment.kind for segment in show.segments] == EXPECTED_KINDS
    assert all(track.id.startswith("jamendo-") for track in show.tracks)
    assert all(track.license.startswith("CC") for track in show.tracks)
    assert all(
        track.track_url.startswith("https://www.jamendo.com/track/") for track in show.tracks
    )
    for track in show.tracks:
        assert (fake_settings.cache_dir / "jamendo" / track.audio_file).exists()
    downloads = [r for r in fake_jamendo.requests if r.url.host == "prod-1.storage.jamendo.com"]
    assert len(downloads) == 3
    assert len(generator.last_responses) == 4  # no wish: no mapping call


def test_jamendo_show_with_a_wish_maps_it_first(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    llm = FakeLLM([FILTERS_JSON, *([CANNED] * 4)])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)

    show = run_generation(
        generator, _show(source="jamendo", wish="piano for a rainy sunday", energy="calm"), tmp_path
    )

    assert show.status == "ready", show.error
    assert "piano for a rainy sunday" in llm.calls[0][0].content
    params = dict(fake_jamendo.requests[0].url.params)
    assert params["fuzzytags"] == "piano rainy"
    assert params["vocalinstrumental"] == "instrumental"
    assert params["speed"] == "verylow low"
    assert len(generator.last_responses) == 5
    # The host hears the wish.
    system = llm.calls[1][0]
    assert "Listeners asked for: piano for a rainy sunday" in system.content


def test_jamendo_show_with_a_bad_wish_reply_fails_readably(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM(["nope"]))

    show = run_generation(generator, _show(source="jamendo", wish="anything"), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "invalid search filters" in show.error


def test_jamendo_error_marks_the_show_failed(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    fake_jamendo.api_error = (6, "Rate Limit Exceeded")
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))

    show = run_generation(generator, _show(source="jamendo"), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "rate limit" in show.error


def test_unconfigured_source_fails_with_the_variable_name(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(
        _generator(fake_settings, tiny_catalog), _show(source="jamendo"), tmp_path
    )

    assert show.status == "failed"
    assert show.error is not None
    assert "JAMENDO_CLIENT_ID" in show.error


def test_wish_on_the_catalog_fails(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(_generator(fake_settings, tiny_catalog), _show(wish="anything"), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "jamendo" in show.error


# Facts looked up on Wikipedia and MusicBrainz (D029). The fake services answer every
# artist with the recorded Kevin MacLeod pages.

WIKIPEDIA_FIRST = "Kevin MacLeod is an American composer and music producer."
JAMENDO_ARTISTS = ("Example Band", "Lea Example", "We Are FM")


def _match_every_jamendo_artist(services: FakeFactServices) -> None:
    """Give the recorded hit the fixture's artist names as aliases, so each one matches."""
    hit = services.replies["musicbrainz_artist_search"]["artists"][0]
    hit["aliases"] = [{"name": name} for name in JAMENDO_ARTISTS]


def test_jamendo_tracks_get_looked_up_facts_after_their_own(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
    tmp_path: Path,
) -> None:
    _match_every_jamendo_artist(fake_facts)
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm, fake_facts)

    show = run_generation(generator, _show(source="jamendo", seed=1), tmp_path / "out")

    assert show.status == "ready", show.error
    for track in show.tracks:
        own = fake_jamendo.source.get_track(track.id).facts
        assert track.facts[: len(own)] == own
        assert len(track.facts) <= MAX_TRACK_FACTS
        assert any(fact.source_type == "wikipedia" for fact in track.facts)
        # The source keeps the track as Jamendo described it.
        assert all(fact.source_type == "jamendo_artist_page" for fact in own)
    # The host reads the Wikipedia sentence in a break.
    assert any(WIKIPEDIA_FIRST in message.content for call in llm.calls for message in call)
    # show.json carries the facts with their links, for the credits.
    saved = load_show(tmp_path / "out")
    assert saved.tracks == show.tracks


def test_a_lookup_miss_keeps_the_jamendo_facts(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
    tmp_path: Path,
) -> None:
    fake_facts.replies["musicbrainz_artist_search"]["artists"] = []
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm, fake_facts)

    show = run_generation(generator, _show(source="jamendo", seed=1), tmp_path / "out")

    assert show.status == "ready", show.error
    for track in show.tracks:
        assert track.facts == fake_jamendo.source.get_track(track.id).facts


def test_a_failed_lookup_does_not_fail_the_show(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
    tmp_path: Path,
) -> None:
    fake_facts.errors["musicbrainz_artist_search"] = 503
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm, fake_facts)

    show = run_generation(generator, _show(source="jamendo", seed=1), tmp_path / "out")

    assert show.status == "ready", show.error


def test_catalog_tracks_are_never_looked_up(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
) -> None:
    generator = _jamendo_generator(
        fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]), fake_facts
    )

    show = generator.write_script(_show(seed=1))

    assert fake_facts.requests == []
    for track in show.tracks:
        assert track.facts == tiny_catalog.get_track(track.id).facts


def _story_breaks_before(generator: PipelineGenerator, track_id: str) -> int:
    """How many of 30 seeded scripts give `track_id` the story break."""
    count = 0
    for seed in range(30):
        show = generator.write_script(_show(source="jamendo", seed=seed))
        count += sum(
            1
            for segment in show.segments
            if segment.kind == "transition"
            and segment.track_id == track_id
            and segment.shape == "story"
        )
    return count


def test_looked_up_facts_open_the_story_break(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
) -> None:
    # "We Are FM" (jamendo-1004) has no usable description: no facts of its own.
    assert fake_jamendo.source.get_track("jamendo-1004").facts == []
    _match_every_jamendo_artist(fake_facts)
    llm = FakeLLM([CANNED])
    without = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    with_lookup = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm, fake_facts)

    assert _story_breaks_before(without, "jamendo-1004") == 0
    assert _story_breaks_before(with_lookup, "jamendo-1004") > 0


def test_merge_facts_drops_repeats_and_caps_the_list() -> None:
    def fact(text: str) -> Fact:
        return Fact(text=text, source_url="https://example.com")

    own = [fact("One."), fact("Two.")]
    found = [fact("two."), fact("Three."), fact("Four."), fact("Five."), fact("Six.")]

    merged = merge_facts(own, found)

    assert [f.text for f in merged] == ["One.", "Two.", "Three.", "Four.", "Five."]


# YouTube shows (D038): the model proposes songs, the source checks them, the songs play in
# the web app, so the pipeline writes the speech files only. A YouTube show has no style:
# the wish is its theme (D039).

PLAYLIST_JSON = (
    '{"songs": ['
    '{"artist": "Example Artist", "title": "Blue Window"}, '
    '{"artist": "Lea Example", "title": "Montreal Streets"}, '
    '{"artist": "We Are FM", "title": "Night Train"}, '
    '{"artist": "Example Records", "title": "Red Lights"}, '
    '{"artist": "River Uploads", "title": "Slow River"}'
    "]}"
)
YOUTUBE_ARTISTS = ("Example Artist", "Lea Example", "We Are FM", "Example Records", "River Uploads")


def _youtube_show(**config) -> Show:
    return _show(**{"style": None, "source": "youtube", "wish": "songs about trains", **config})


def _youtube_generator(
    settings: Settings,
    catalog: LocalCatalog,
    youtube: FakeYouTube,
    llm: FakeLLM,
    facts: FakeFactServices | None = None,
) -> PipelineGenerator:
    tts = CachedTextToSpeech(FakeTTS(settings.cache_dir / "tts-raw"), settings.cache_dir)
    sources = {"catalog": catalog, "youtube": youtube.source}
    lookup = facts.lookup if facts is not None else None
    return PipelineGenerator(llm, tts, sources, settings, facts=lookup)


def test_youtube_show_reaches_ready_without_an_mp3(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    progress: list[tuple[int, int]] = []
    out_dir = tmp_path / "out"

    show = run_generation(
        generator, _youtube_show(seed=1), out_dir, lambda d, t: progress.append((d, t))
    )

    assert show.status == "ready", show.error
    assert show.audio_file is None
    assert not (out_dir / "show.mp3").exists()
    assert [segment.kind for segment in show.segments] == EXPECTED_KINDS
    tracks_by_id = {track.id: track for track in show.tracks}
    for index, segment in enumerate(show.segments):
        if segment.kind == "song":
            assert segment.audio_file is None
            assert segment.duration_s == tracks_by_id[segment.track_id or ""].duration_s
        else:
            assert segment.audio_file == f"{index:02d}-{segment.kind}.mp3"
            assert (out_dir / segment.audio_file).exists()
            assert segment.duration_s is not None and segment.duration_s > 0
            assert segment.text == CANNED
    assert sorted(path.name for path in out_dir.iterdir() if path.suffix == ".mp3") == [
        "00-intro.mp3",
        "02-transition.mp3",
        "04-transition.mp3",
        "06-outro.mp3",
    ]
    assert len(show.tracks) == 3
    for track in show.tracks:
        assert track.id.startswith("youtube-")
        assert track.video_id and track.audio_file is None
        assert track.license == "YouTube embed"
        assert track.style == ""  # no style on YouTube (D039); Track needs a str
    # Five proposals asked, three searched: the spare ones cost no quota.
    assert "Number of songs: 5" in llm.calls[0][0].content
    assert len(fake_youtube.searches()) == 3
    assert len(generator.last_responses) == 5  # the playlist and four scripts
    assert progress == [(done, 7) for done in range(8)]
    assert load_show(out_dir).model_dump() == show.model_dump()


def test_youtube_demo_show_plays_thirty_seconds_of_each_song(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(demo=True), tmp_path / "out")

    assert show.status == "ready", show.error
    songs = [segment for segment in show.segments if segment.kind == "song"]
    assert [segment.duration_s for segment in songs] == [30.0, 30.0, 30.0]
    assert all(track.duration_s > 30 for track in show.tracks)  # the track keeps its length


def test_youtube_show_with_a_wish_passes_it_to_the_playlist_and_the_host(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(energy="upbeat"), tmp_path)

    assert show.status == "ready", show.error
    playlist_prompt = llm.calls[0][0].content
    assert "The listener's request: songs about trains" in playlist_prompt
    assert "Energy chosen by the listener: upbeat" in playlist_prompt
    assert "Listeners asked for: songs about trains" in llm.calls[1][0].content


def test_youtube_show_without_a_style_renders_no_none_in_the_prompts(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    """The wish is the theme (D039): the prompts say so and never show a Python None."""
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(wish="90s French rap, upbeat"), tmp_path)

    assert show.status == "ready", show.error
    assert show.config.style is None
    assert load_show(tmp_path).config.style is None
    playlist_prompt = llm.calls[0][0].content
    assert "Style chosen by the listener: (none: the request is the theme)" in playlist_prompt
    assert "The listener's request: 90s French rap, upbeat" in playlist_prompt
    system_prompt = llm.calls[1][0].content
    assert (
        "The show has no set style tonight: its theme is what the listeners asked for."
        in system_prompt
    )
    assert "Listeners asked for: 90s French rap, upbeat" in system_prompt
    for call in llm.calls:
        for message in call:
            assert "None" not in message.content


def test_youtube_show_with_too_few_songs_names_the_source_not_a_style(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    playlist = '{"songs": [{"artist": "Lea Example", "title": "Montreal Streets"}]}'
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, FakeLLM([playlist]))

    show = run_generation(generator, _youtube_show(), tmp_path)

    assert show.status == "failed"
    assert show.error == "Source 'youtube' has 1 tracks, 3 needed."


def test_a_catalog_show_without_a_style_fails_readably(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(_generator(fake_settings, tiny_catalog), _show(style=None), tmp_path)

    assert show.status == "failed"
    assert show.error == "Source 'catalog' needs a style."


def test_a_youtube_show_without_a_wish_fails_readably(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube
) -> None:
    """The API and the CLI refuse it first; a show built in code gets the same answer."""
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, FakeLLM([CANNED]))

    with pytest.raises(GenerationError, match="Source 'youtube' needs a wish"):
        generator.write_script(_youtube_show(wish=None))

    assert generator.last_responses == []  # no playlist asked for
    assert fake_youtube.requests == []


def test_youtube_quota_marks_the_show_failed_naming_the_limit(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    fake_youtube.quota_exceeded = True
    llm = FakeLLM([PLAYLIST_JSON])  # the quota stops the show before any script
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "100 searches a day" in show.error
    with pytest.raises(GenerationError, match="100 searches"):
        generator.generate(_youtube_show(), tmp_path / "again")


def test_youtube_show_with_a_bad_playlist_reply_fails_readably(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, FakeLLM(["nope"]))

    show = run_generation(generator, _youtube_show(), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "invalid playlist" in show.error
    assert fake_youtube.requests == []


def test_youtube_show_with_too_few_playable_songs_fails(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    playlist = (
        '{"songs": [{"artist": "Nobody", "title": "Broken Song"}, '
        '{"artist": "Nobody", "title": "Never Recorded"}, '
        '{"artist": "Lea Example", "title": "Montreal Streets"}]}'
    )
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, FakeLLM([playlist]))

    show = run_generation(generator, _youtube_show(), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "1 tracks, 3 needed" in show.error


def test_write_script_works_for_youtube(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = generator.write_script(_youtube_show(seed=1))

    assert [segment.kind for segment in show.segments] == EXPECTED_KINDS
    assert all(segment.audio_file is None for segment in show.segments)
    assert all(track.video_id for track in show.tracks)
    assert len(generator.last_responses) == 5
    trace_dir = tmp_path / "output" / "scripts" / show.id
    assert sorted(path.name for path in trace_dir.iterdir()) == ["llm.jsonl", "youtube.jsonl"]


def test_youtube_artists_get_looked_up_facts(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_youtube: FakeYouTube,
    fake_facts: FakeFactServices,
    tmp_path: Path,
) -> None:
    hit = fake_facts.replies["musicbrainz_artist_search"]["artists"][0]
    hit["aliases"] = [{"name": name} for name in YOUTUBE_ARTISTS]
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm, fake_facts)

    show = run_generation(generator, _youtube_show(seed=1), tmp_path / "out")

    assert show.status == "ready", show.error
    for track in show.tracks:
        assert any(fact.source_type == "wikipedia" for fact in track.facts)
        assert all(fact.about == "artist" for fact in track.facts)
    assert any(WIKIPEDIA_FIRST in message.content for call in llm.calls for message in call)


def test_unconfigured_youtube_fails_with_the_variable_name(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(_generator(fake_settings, tiny_catalog), _youtube_show(), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "YOUTUBE_API_KEY" in show.error


# The endless show (D042, D044): `generate` makes the opening, the intro and the first
# song, and each `extend` call adds one break and one song, or the closing. The catalog is
# excluded, so these run on the fake Jamendo and YouTube transports.


def _endless_show(**config) -> Show:
    return _show(**{"source": "jamendo", "endless": True, "seed": 1, **config})


def _step(generator: PipelineGenerator, out_dir: Path, *, end: bool = False) -> Show:
    """One step the way the API runs it: read show.json, extend the show, save it."""
    show = generator.extend(load_show(out_dir), out_dir, end=end)
    save_show(show, out_dir)
    return show


def _kinds(show: Show) -> list[str]:
    return [segment.kind for segment in show.segments]


def _audio_files(out_dir: Path) -> list[str]:
    return sorted(path.name for path in out_dir.iterdir() if path.suffix == ".mp3")


def _trace(out_dir: Path, service: str) -> list[dict]:
    lines = (out_dir / "trace" / f"{service}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_an_endless_show_opens_with_an_intro_and_one_song(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    progress: list[tuple[int, int]] = []
    out_dir = tmp_path / "out"

    show = run_generation(generator, _endless_show(), out_dir, lambda d, t: progress.append((d, t)))

    assert show.status == "ready", show.error
    assert _kinds(show) == ["intro", "song"]
    assert show.is_open
    assert show.end_note is None
    # No show.mp3: a show that grows plays segment by segment, the songs from files too.
    assert show.audio_file is None
    assert _audio_files(out_dir) == ["00-intro.mp3", "01-song.mp3"]
    intro, song = show.segments
    assert intro.text == CANNED and intro.audio_file == "00-intro.mp3"
    assert song.audio_file == "01-song.mp3" and song.track_id == show.tracks[0].id
    assert song.duration_s is not None and abs(song.duration_s - TONE_SECONDS) <= 0.3
    # The count is the songs so far, not the three a show has by default.
    assert len(show.tracks) == 1
    assert show.config.track_count == 1
    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert len(generator.last_responses) == 1
    downloads = [r for r in fake_jamendo.requests if r.url.host == "prod-1.storage.jamendo.com"]
    assert len(downloads) == 1
    # The intro of a show with no set length, about its first song only.
    _, user = llm.calls[0]
    assert "It has no set length" in user.content
    assert show.tracks[0].title in user.content
    assert load_show(out_dir).model_dump() == show.model_dump()


def test_extend_adds_a_break_and_a_song_and_leaves_the_rest_alone(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    out_dir = tmp_path / "out"
    opened = run_generation(generator, _endless_show(), out_dir).model_copy(deep=True)
    earlier = {name: (out_dir / name).read_bytes() for name in _audio_files(out_dir)}

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song"]
    assert show.status == "ready" and show.is_open
    assert show.segments[:2] == opened.segments
    assert show.tracks[:1] == opened.tracks
    assert _audio_files(out_dir) == [
        "00-intro.mp3",
        "01-song.mp3",
        "02-transition.mp3",
        "03-song.mp3",
    ]
    for name, content in earlier.items():
        assert (out_dir / name).read_bytes() == content
    transition, song = show.segments[2:]
    assert transition.text == CANNED and transition.shape
    assert transition.audio_file == "02-transition.mp3"
    assert transition.track_id == song.track_id == show.tracks[1].id
    assert song.audio_file == "03-song.mp3"
    assert show.tracks[1].id != show.tracks[0].id
    assert show.config.track_count == 2
    assert show.audio_file is None and not (out_dir / "show.mp3").exists()
    # The break hands over from the song that played to the new one.
    _, user = llm.calls[-1]
    assert f"Previous song: {show.tracks[0].display_title}" in user.content
    assert f"Next song: {show.tracks[1].display_title}" in user.content
    assert len(generator.last_responses) == 1  # the responses of this step only
    # The step traces into the show's own folder (D037).
    assert [record["label"] for record in _trace(out_dir, "llm")] == [
        "intro",
        f"transition before {show.tracks[1].display_title} ({transition.shape})",
    ]


def test_an_endless_show_never_repeats_a_song_and_closes_when_the_source_is_empty(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    """The Jamendo fixture has four jazz tracks: the fifth request gets the goodbye."""
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)

    for _ in range(3):
        show = _step(generator, out_dir)

    assert sorted(track.id for track in show.tracks) == [
        "jamendo-1001",
        "jamendo-1002",
        "jamendo-1003",
        "jamendo-1004",
    ]
    assert show.is_open and show.end_note is None

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", *(["transition", "song"] * 3), "outro"]
    assert not show.is_open
    assert show.end_note == END_NOTE_NO_SONGS
    assert show.status == "ready"
    assert show.config.track_count == 4
    outro = show.segments[-1]
    assert outro.text == CANNED and outro.audio_file == "08-outro.mp3"
    assert (out_dir / "08-outro.mp3").exists()
    # A closed show takes no more steps.
    with pytest.raises(GenerationError, match="not an endless show that is still open"):
        generator.extend(load_show(out_dir), out_dir)


def test_the_listener_can_end_an_endless_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    llm = FakeLLM(["Opening words.", "Goodbye words."])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)

    show = _step(generator, out_dir, end=True)

    assert _kinds(show) == ["intro", "song", "outro"]
    assert not show.is_open
    assert show.end_note is None  # the listener ended it: nothing to explain
    assert show.segments[-1].text == "Goodbye words."
    assert show.config.track_count == 1
    _, user = llm.calls[-1]
    assert "The show is over." in user.content
    assert show.tracks[0].display_title in user.content
    assert "- Opening: Opening words." in user.content


def test_a_new_generator_goes_on_from_show_json(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a restart nothing is in memory: the songs played, the scripts and the last
    shape all come back from show.json."""
    previous_shapes: list[str | None] = []

    def recording_plan_shapes(shapes, facts_available, rng, previous=None):
        previous_shapes.append(previous)
        return plan_shapes(shapes, facts_available, rng, previous)

    monkeypatch.setattr(pipeline, "plan_shapes", recording_plan_shapes)
    first_llm = FakeLLM(["Opening words.", "First break."])
    out_dir = tmp_path / "out"
    first = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, first_llm)
    run_generation(first, _endless_show(), out_dir)
    _step(first, out_dir)

    # The restart: another generator, another Jamendo source, another LLM client.
    tone = (tmp_path / "jamendo-tone.mp3").read_bytes()
    second_llm = FakeLLM(["Second break."])
    second = _jamendo_generator(
        fake_settings, tiny_catalog, FakeJamendo(tmp_path / "cache", tone), second_llm
    )
    show = _step(second, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song", "transition", "song"]
    assert len({track.id for track in show.tracks}) == 3
    assert [s.text for s in show.segments if s.kind != "song"] == [
        "Opening words.",
        "First break.",
        "Second break.",
    ]
    _, user = second_llm.calls[0]
    assert "- Opening: Opening words." in user.content
    assert "- Break: First break." in user.content
    assert f"Previous song: {show.tracks[1].display_title}" in user.content
    first_break, second_break = (s for s in show.segments if s.kind == "transition")
    assert previous_shapes == [None, first_break.shape]
    assert second_break.shape != first_break.shape


def test_a_break_of_a_long_show_gets_the_last_scripts_and_songs_only(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The show memory is capped (D044). With a memory of two scripts the cap shows on
    a show of four songs."""
    monkeypatch.setattr(writer, "TRANSCRIPT_MEMORY", 2)
    monkeypatch.setattr(pipeline, "TRANSCRIPT_MEMORY", 2)
    llm = FakeLLM(["Script 0.", "Script 1.", "Script 2.", "Script 3.", "Goodbye."])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    for _ in range(3):
        _step(generator, out_dir)

    show = _step(generator, out_dir, end=True)

    assert len(llm.calls) == 5
    third_break = llm.calls[3][1].content
    assert "Script 0." not in third_break
    assert "- Break: Script 1." in third_break
    assert "- Break: Script 2." in third_break
    outro = llm.calls[4][1].content
    assert "Script 1." not in outro
    assert "- Break: Script 2." in outro
    assert "- Break: Script 3." in outro
    # The programme of the outro is cut the same way.
    first, second, third, fourth = show.tracks
    assert first.title not in outro and second.title not in outro
    assert third.title in outro and fourth.title in outro


def test_the_song_limit_closes_an_endless_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    settings = fake_settings.model_copy(update={"endless_max_songs": 2})
    generator = _jamendo_generator(settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    show = _step(generator, out_dir)
    assert _kinds(show) == ["intro", "song", "transition", "song"]
    requests_before = len(fake_jamendo.requests)

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song", "outro"]
    assert show.end_note == "The show ended by itself: it reached the limit of 2 songs."
    assert len(show.tracks) == 2
    assert len(fake_jamendo.requests) == requests_before  # no search for a song it cannot play


def test_a_song_limit_of_zero_is_no_limit(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    settings = fake_settings.model_copy(update={"endless_max_songs": 0})
    generator = _jamendo_generator(settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)

    for _ in range(3):
        show = _step(generator, out_dir)

    assert len(show.tracks) == 4
    assert show.is_open and show.end_note is None


def test_a_failed_step_leaves_the_show_as_it_was_and_can_be_asked_again(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    saved = (out_dir / "show.json").read_bytes()
    failing = PipelineGenerator(
        FakeLLM([CANNED]),
        FailingTTS(),
        {"catalog": tiny_catalog, "jamendo": fake_jamendo.source},
        fake_settings,
    )

    with pytest.raises(TTSError, match="voice is out"):
        failing.extend(load_show(out_dir), out_dir)

    assert (out_dir / "show.json").read_bytes() == saved
    assert _audio_files(out_dir) == ["00-intro.mp3", "01-song.mp3"]

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song"]
    # steps.jsonl holds one line per step, the failed one included: its only record.
    steps = _trace(out_dir, "steps")
    assert [
        (step["kind"], step["segments_before"], step["segments_after"], step["error"])
        for step in steps
    ] == [
        ("opening", 0, 2, None),
        ("next", 2, 2, "Fake TTS: the voice is out today"),
        ("next", 2, 4, None),
    ]
    assert [step["track_id"] for step in steps] == [show.tracks[0].id, None, show.tracks[1].id]
    assert all(step["end_note"] is None for step in steps)
    assert all(step["latency_s"] >= 0 and step["ts"] for step in steps)


def test_the_closing_step_is_recorded_with_its_end_note(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    settings = fake_settings.model_copy(update={"endless_max_songs": 1})
    generator = _jamendo_generator(settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "outro"]
    closing = _trace(out_dir, "steps")[-1]
    assert closing["kind"] == "next"  # asked for a song, got the goodbye
    assert closing["track_id"] is None
    assert closing["end_note"] == show.end_note
    assert (closing["segments_before"], closing["segments_after"]) == (2, 3)


class SteppingClock:
    """Stands in for `datetime` in the pipeline: each `now()` gives the next time."""

    def __init__(self, times: list[datetime]) -> None:
        self._times = iter(times)

    def now(self, tz: object = None) -> datetime:
        return next(self._times)


def test_the_host_reads_the_clock_at_each_step(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A show can run for hours, so the part of the day may change on the way (D044)."""
    morning = datetime(2026, 11, 16, 11, 30, tzinfo=UTC)
    evening = datetime(2026, 11, 16, 19, 0, tzinfo=UTC)
    monkeypatch.setattr(pipeline, "datetime", SteppingClock([morning, evening]))
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    out_dir = tmp_path / "out"

    run_generation(generator, _endless_show(), out_dir)
    _step(generator, out_dir)

    opening_system, step_system = (call[0].content for call in llm.calls)
    assert "in the morning" in opening_system
    assert "in the evening" in step_system and "in the morning" not in step_system


def test_an_endless_jamendo_show_maps_the_wish_once(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    """The filters stay in memory between two steps. A restart loses them, which costs
    one more mapping call and nothing else."""
    llm = FakeLLM([FILTERS_JSON, CANNED, CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(wish="piano for a rainy sunday"), out_dir)

    _step(generator, out_dir)

    labels = [record["label"].split(" ")[0] for record in _trace(out_dir, "llm")]
    assert labels == ["wish", "intro", "transition"]
    searches = [r for r in fake_jamendo.requests if r.url.path == "/v3.0/tracks/"]
    assert [r.url.params["fuzzytags"] for r in searches] == ["piano rainy", "piano rainy"]

    restarted = _jamendo_generator(
        fake_settings, tiny_catalog, fake_jamendo, FakeLLM([FILTERS_JSON, CANNED])
    )
    show = _step(restarted, out_dir)

    assert len(show.tracks) == 3
    labels = [record["label"].split(" ")[0] for record in _trace(out_dir, "llm")]
    assert labels == ["wish", "intro", "transition", "wish", "transition"]


def test_each_new_song_of_an_endless_show_gets_its_facts(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
    tmp_path: Path,
) -> None:
    _match_every_jamendo_artist(fake_facts)
    llm = FakeLLM([CANNED])
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, llm, fake_facts)
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)

    show = _step(generator, out_dir)

    for track in show.tracks:
        assert any(fact.source_type == "wikipedia" for fact in track.facts)
    # The break before the new song is written from them.
    _, user = llm.calls[-1]
    assert WIKIPEDIA_FIRST in user.content


def test_an_endless_show_on_the_catalog_fails_readably(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    """The API refuses it first (D042); a show built in code gets the same answer."""
    generator = _generator(fake_settings, tiny_catalog)

    show = run_generation(generator, _show(endless=True), tmp_path)

    assert show.status == "failed"
    assert show.error == "An endless show needs the 'jamendo' or 'youtube' music source."
    assert generator.last_responses == []


def test_a_show_of_a_set_length_cannot_be_extended(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    show = run_generation(generator, _show(source="jamendo", seed=1), out_dir)
    assert show.status == "ready", show.error

    with pytest.raises(GenerationError, match="not an endless show that is still open"):
        generator.extend(load_show(out_dir), out_dir)

    assert load_show(out_dir).model_dump() == show.model_dump()


# The endless show on YouTube: the model proposes eight songs at a time, each one is
# searched when its turn comes, and the next playlist is told what was played.


def _playlist(*songs: tuple[str, str]) -> str:
    return json.dumps({"songs": [{"artist": artist, "title": title} for artist, title in songs]})


BLUE_WINDOW = ("Example Artist", "Blue Window")
MONTREAL = ("Lea Example", "Montreal Streets")
NIGHT_TRAIN = ("We Are FM", "Night Train")
RED_LIGHTS = ("Example Records", "Red Lights")
SLOW_RIVER = ("River Uploads", "Slow River")


def _playlist_prompts(llm: FakeLLM) -> list[str]:
    """The playlist calls: one user message, no system prompt."""
    return [call[0].content for call in llm.calls if len(call) == 1]


def test_an_endless_youtube_show_opens_and_uses_its_proposals_one_at_a_time(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([_playlist(BLUE_WINDOW, MONTREAL, NIGHT_TRAIN), CANNED, CANNED, CANNED])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    out_dir = tmp_path / "out"

    show = run_generation(generator, _youtube_show(endless=True), out_dir)

    assert show.status == "ready", show.error
    assert _kinds(show) == ["intro", "song"]
    assert [track.video_id for track in show.tracks] == ["okTopic0001"]
    song = show.segments[1]
    assert song.audio_file is None and song.duration_s == show.tracks[0].duration_s
    assert _audio_files(out_dir) == ["00-intro.mp3"]
    assert show.config.track_count == 1
    # Eight songs asked, one searched: the others wait for their turn.
    assert "Number of songs: 8" in llm.calls[0][0].content
    assert "Never propose one of them again:\n(nothing yet)" in llm.calls[0][0].content
    assert len(fake_youtube.searches()) == 1

    show = _step(generator, out_dir)
    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song", "transition", "song"]
    assert [track.video_id for track in show.tracks] == [
        "okTopic0001",
        "montreal001",
        "nightTrn001",
    ]
    assert _audio_files(out_dir) == ["00-intro.mp3", "02-transition.mp3", "04-transition.mp3"]
    assert len(fake_youtube.searches()) == 3  # one per song
    assert len(_playlist_prompts(llm)) == 1  # the proposals of the opening were enough
    assert show.config.track_count == 3


def test_the_next_playlist_is_told_what_was_played_and_a_repeat_costs_no_search(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    again = ("example artist", "Blue  WINDOW")  # the same song, typed another way
    llm = FakeLLM(
        [_playlist(BLUE_WINDOW, MONTREAL), CANNED, CANNED, _playlist(again, NIGHT_TRAIN), CANNED]
    )
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _youtube_show(endless=True, demo=True), out_dir)
    _step(generator, out_dir)
    assert len(fake_youtube.searches()) == 2

    show = _step(generator, out_dir)  # the first playlist is used up: a second one is asked

    assert [track.video_id for track in show.tracks] == [
        "okTopic0001",
        "montreal001",
        "nightTrn001",
    ]
    first, second = _playlist_prompts(llm)
    assert "(nothing yet)" in first
    assert (
        "Never propose one of them again:\n"
        "Blue Window by Example Artist\n"
        "Montreal Streets by Lea Example\n"
    ) in second
    # Blue Window was proposed again: skipped by its name, with no search.
    assert len(fake_youtube.searches()) == 3
    # A demo show plays thirty seconds of each video.
    assert [s.duration_s for s in show.segments if s.kind == "song"] == [30.0, 30.0, 30.0]


def test_a_proposal_that_leads_to_a_played_video_is_skipped(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    """Another name for a song the show played: only the search can tell (D044)."""
    river_again = ("Someone Else", "Slow River")
    llm = FakeLLM([_playlist(SLOW_RIVER, river_again, MONTREAL), CANNED, CANNED])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _youtube_show(endless=True), out_dir)

    show = _step(generator, out_dir)

    assert [track.video_id for track in show.tracks] == ["slowRivr001", "montreal001"]
    assert len(fake_youtube.searches()) == 3


def test_two_playlists_with_nothing_new_close_an_endless_youtube_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM(
        [
            _playlist(BLUE_WINDOW),
            "Opening words.",
            _playlist(BLUE_WINDOW),  # played already
            _playlist(("Nobody", "Broken Song")),  # no playable video
            "Goodbye words.",
        ]
    )
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _youtube_show(endless=True), out_dir)

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "outro"]
    assert show.end_note == END_NOTE_NO_SONGS
    assert show.segments[-1].text == "Goodbye words."
    assert len(_playlist_prompts(llm)) == 3  # the opening's, then two with nothing new
    assert len(llm.calls) == 5


def test_the_youtube_quota_closes_an_endless_show_in_a_later_step(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([_playlist(BLUE_WINDOW, MONTREAL), CANNED, CANNED])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _youtube_show(endless=True), out_dir)
    fake_youtube.quota_exceeded = True

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "outro"]
    assert show.status == "ready" and not show.is_open
    assert show.end_note == END_NOTE_QUOTA
    assert _audio_files(out_dir) == ["00-intro.mp3", "02-outro.mp3"]


def test_the_youtube_quota_fails_an_endless_show_in_its_opening(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    fake_youtube.quota_exceeded = True
    llm = FakeLLM([_playlist(BLUE_WINDOW, MONTREAL)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(endless=True), tmp_path / "out")

    assert show.status == "failed"
    assert show.error is not None and "100 searches a day" in show.error
    assert show.segments == [] and show.end_note is None
    opening = _trace(tmp_path / "out", "steps")[0]
    assert opening["kind"] == "opening" and "100 searches a day" in opening["error"]


def test_an_endless_youtube_show_with_no_playable_song_fails_in_its_opening(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([_playlist(("Nobody", "Broken Song"))])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(endless=True), tmp_path / "out")

    assert show.status == "failed"
    assert show.error == "Source 'youtube' has 0 tracks, 1 needed."
    assert len(_playlist_prompts(llm)) == 2  # two playlists with nothing playable


def test_the_proposals_in_memory_belong_to_one_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    """Two endless shows open at once still work: the one that did not ask last gets a
    new playlist, at the cost of one LLM call (D044)."""
    llm = FakeLLM(
        [
            _playlist(BLUE_WINDOW, MONTREAL),  # show A
            CANNED,
            _playlist(RED_LIGHTS, SLOW_RIVER),  # show B
            CANNED,
            _playlist(NIGHT_TRAIN),  # show A again
            CANNED,
        ]
    )
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    show_a = _youtube_show(endless=True)
    show_b = _youtube_show(endless=True).model_copy(update={"id": "other-show"})
    run_generation(generator, show_a, tmp_path / "a")
    run_generation(generator, show_b, tmp_path / "b")

    show = _step(generator, tmp_path / "a")

    assert [track.video_id for track in show.tracks] == ["okTopic0001", "nightTrn001"]
    third = _playlist_prompts(llm)[2]
    assert "Blue Window by Example Artist" in third
    assert "Red Lights" not in third


def test_a_failed_youtube_step_keeps_its_song_and_asking_again_costs_no_search(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    """The proposal a step used stays first in the list until the step has succeeded
    (D044). The second try gets the same song, answered by the search cache."""
    llm = FakeLLM([_playlist(BLUE_WINDOW, MONTREAL, NIGHT_TRAIN), "Opening words.", "", "A break."])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)
    out_dir = tmp_path / "out"
    run_generation(generator, _youtube_show(endless=True), out_dir)
    saved = (out_dir / "show.json").read_bytes()

    # The search finds Montreal Streets, then the model returns an empty script.
    with pytest.raises(LLMError, match="empty transition script"):
        generator.extend(load_show(out_dir), out_dir)

    assert len(fake_youtube.searches()) == 2
    assert (out_dir / "show.json").read_bytes() == saved

    show = _step(generator, out_dir)

    assert [track.video_id for track in show.tracks] == ["okTopic0001", "montreal001"]
    assert len(fake_youtube.searches()) == 2  # no new search
    assert len(_playlist_prompts(llm)) == 1  # and no new playlist
    choices = [(r["method"], r["title"]) for r in _trace(out_dir, "youtube") if "title" in r]
    assert choices[-2:] == [("pick", "Montreal Streets"), ("cache", "Montreal Streets")]

    # The step after it drops the used proposal for free and moves on to the next one.
    show = _step(generator, out_dir)

    assert [track.video_id for track in show.tracks][2:] == ["nightTrn001"]
    assert len(fake_youtube.searches()) == 3


def test_a_playlist_longer_than_asked_is_cut_to_the_batch(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    """The model decides how many songs it writes. Only eight are kept, so what one step
    can spend on searches does not depend on the model (D044)."""
    no_video = [(f"Nobody {number}", "Broken Song") for number in range(1, 9)]
    llm = FakeLLM([_playlist(*no_video, MONTREAL), _playlist(BLUE_WINDOW), CANNED])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _youtube_show(endless=True), tmp_path / "out")

    assert show.status == "ready", show.error
    # Montreal Streets was the ninth song of the first playlist: never looked up.
    assert [track.video_id for track in show.tracks] == ["okTopic0001"]
    queries = [request.url.params["q"].casefold() for request in fake_youtube.searches()]
    assert len(queries) == 9  # eight songs with no video, then Blue Window
    assert not any("montreal" in query for query in queries)
    assert len(_playlist_prompts(llm)) == 2


# More about one step, on Jamendo: a seeded show, a failure in ffmpeg, a lowered limit.


def test_a_seeded_endless_show_is_the_same_show_step_by_step(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    """Each step seeds its own random generator from the show's seed and the number of
    songs so far (D044), so the same seed gives the same songs and the same shapes."""
    shows = []
    for name in ("a", "b"):
        generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
        out_dir = tmp_path / name
        run_generation(generator, _endless_show(seed=5), out_dir)
        _step(generator, out_dir)
        shows.append(_step(generator, out_dir))

    first, second = shows
    assert len(first.tracks) == 3
    assert [track.id for track in first.tracks] == [track.id for track in second.tracks]
    assert [segment.shape for segment in first.segments] == [s.shape for s in second.segments]
    assert _kinds(first) == _kinds(second)


def test_a_step_that_fails_in_ffmpeg_leaves_the_show_as_it_was(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ffmpeg is the last thing a step does. When it fails on the song, the break's file
    is already there: show.json does not name it, and the next try writes over it."""
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    saved = (out_dir / "show.json").read_bytes()

    def broken_song(src: Path, dst: Path, demo: bool) -> None:
        raise AudioError("ffmpeg failed: fake")

    monkeypatch.setattr(assemble, "prepare_song", broken_song)
    with pytest.raises(AudioError, match="ffmpeg failed"):
        generator.extend(load_show(out_dir), out_dir)
    monkeypatch.undo()

    assert (out_dir / "show.json").read_bytes() == saved
    assert _kinds(load_show(out_dir)) == ["intro", "song"]
    assert _audio_files(out_dir) == ["00-intro.mp3", "01-song.mp3", "02-transition.mp3"]

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song"]
    assert _audio_files(out_dir) == [
        "00-intro.mp3",
        "01-song.mp3",
        "02-transition.mp3",
        "03-song.mp3",
    ]
    errors = [step["error"] for step in _trace(out_dir, "steps")]
    assert errors == [None, "ffmpeg failed: fake", None]


def test_a_show_already_past_a_lowered_song_limit_closes_at_its_next_step(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    """ENDLESS_MAX_SONGS can be lowered between two runs of the server."""
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    _step(generator, out_dir)
    assert len(_step(generator, out_dir).tracks) == 3
    lowered = fake_settings.model_copy(update={"endless_max_songs": 2})
    restarted = _jamendo_generator(lowered, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    requests_before = len(fake_jamendo.requests)

    show = _step(restarted, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song", "transition", "song", "outro"]
    assert show.end_note == "The show ended by itself: it reached the limit of 2 songs."
    assert len(show.tracks) == 3  # nothing is taken back
    assert len(fake_jamendo.requests) == requests_before


# False empty replies from Jamendo (D044): now and then, in runs, the search answers no
# results for a style that has thousands. A show must never take that for the end of the
# music.


def test_a_run_of_false_empty_replies_does_not_close_an_endless_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    fake_jamendo.empty_replies = 4  # more than the three the host used to sign off on

    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song"]
    assert show.is_open and show.end_note is None
    assert len({track.id for track in show.tracks}) == 2


def test_a_step_fails_when_jamendo_keeps_answering_nothing_and_can_be_asked_again(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))
    out_dir = tmp_path / "out"
    run_generation(generator, _endless_show(), out_dir)
    saved = (out_dir / "show.json").read_bytes()
    fake_jamendo.empty_replies = 99

    with pytest.raises(JamendoError, match="Jamendo did not answer the search"):
        generator.extend(load_show(out_dir), out_dir)

    # No goodbye and no note: the show is as it was, still open.
    assert (out_dir / "show.json").read_bytes() == saved
    assert load_show(out_dir).is_open
    assert _audio_files(out_dir) == ["00-intro.mp3", "01-song.mp3"]
    assert len(generator.last_responses) == 0  # no script was paid for

    fake_jamendo.empty_replies = 0
    show = _step(generator, out_dir)

    assert _kinds(show) == ["intro", "song", "transition", "song"]
    errors = [step["error"] for step in _trace(out_dir, "steps")]
    assert errors == [None, "Jamendo did not answer the search. Try again in a moment.", None]


def test_an_endless_show_does_not_open_when_jamendo_keeps_answering_nothing(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    """The opening fails with what happened, not with "the style has 0 tracks"."""
    fake_jamendo.empty_replies = 99
    generator = _jamendo_generator(fake_settings, tiny_catalog, fake_jamendo, FakeLLM([CANNED]))

    show = run_generation(generator, _endless_show(), tmp_path / "out")

    assert show.status == "failed"
    assert show.error == "Jamendo did not answer the search. Try again in a moment."
    assert generator.last_responses == []
