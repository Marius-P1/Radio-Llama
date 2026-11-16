import random
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from radio_llama.config import Settings
from radio_llama.generator import GenerationError, PipelineGenerator, run_generation
from radio_llama.generator.pipeline import MAX_TRACK_FACTS, merge_facts, pick_tracks
from radio_llama.llm import FakeLLM
from radio_llama.models import Fact, Show, ShowConfig, Track
from radio_llama.music import LocalCatalog
from radio_llama.storage import load_show
from radio_llama.voice import CachedTextToSpeech, FakeTTS, TextToSpeech, TTSError, Voice
from tests.conftest import FakeFactServices, FakeJamendo, FakeYouTube

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
        config=ShowConfig(style="jazz", persona="calm", **config),
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
# the web app, so the pipeline writes the speech files only.

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
        generator, _show(source="youtube", seed=1), out_dir, lambda d, t: progress.append((d, t))
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
        assert track.style == "jazz"
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

    show = run_generation(generator, _show(source="youtube", demo=True), tmp_path / "out")

    assert show.status == "ready", show.error
    songs = [segment for segment in show.segments if segment.kind == "song"]
    assert [segment.duration_s for segment in songs] == [30.0, 30.0, 30.0]
    assert all(track.duration_s > 30 for track in show.tracks)  # the track keeps its length


def test_youtube_show_with_a_wish_passes_it_to_the_playlist_and_the_host(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(
        generator, _show(source="youtube", wish="songs about trains", energy="upbeat"), tmp_path
    )

    assert show.status == "ready", show.error
    playlist_prompt = llm.calls[0][0].content
    assert "The listener's request: songs about trains" in playlist_prompt
    assert "Energy chosen by the listener: upbeat" in playlist_prompt
    assert "Listeners asked for: songs about trains" in llm.calls[1][0].content


def test_youtube_quota_marks_the_show_failed_naming_the_limit(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    fake_youtube.quota_exceeded = True
    llm = FakeLLM([PLAYLIST_JSON])  # the quota stops the show before any script
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = run_generation(generator, _show(source="youtube"), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "100 searches a day" in show.error
    with pytest.raises(GenerationError, match="100 searches"):
        generator.generate(_show(source="youtube"), tmp_path / "again")


def test_youtube_show_with_a_bad_playlist_reply_fails_readably(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, FakeLLM(["nope"]))

    show = run_generation(generator, _show(source="youtube"), tmp_path)

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

    show = run_generation(generator, _show(source="youtube"), tmp_path)

    assert show.status == "failed"
    assert show.error is not None
    assert "1 tracks, 3 needed" in show.error


def test_write_script_works_for_youtube(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    llm = FakeLLM([PLAYLIST_JSON, *([CANNED] * 4)])
    generator = _youtube_generator(fake_settings, tiny_catalog, fake_youtube, llm)

    show = generator.write_script(_show(source="youtube", seed=1))

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

    show = run_generation(generator, _show(source="youtube", seed=1), tmp_path / "out")

    assert show.status == "ready", show.error
    for track in show.tracks:
        assert any(fact.source_type == "wikipedia" for fact in track.facts)
        assert all(fact.about == "artist" for fact in track.facts)
    assert any(WIKIPEDIA_FIRST in message.content for call in llm.calls for message in call)


def test_unconfigured_youtube_fails_with_the_variable_name(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    show = run_generation(
        _generator(fake_settings, tiny_catalog), _show(source="youtube"), tmp_path
    )

    assert show.status == "failed"
    assert show.error is not None
    assert "YOUTUBE_API_KEY" in show.error
