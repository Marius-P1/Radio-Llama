"""The per-show trace of every API call (D037), on the fakes and the mock transports."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from radio_llama import trace
from radio_llama.config import Settings
from radio_llama.generator import PipelineGenerator, run_generation
from radio_llama.llm import FakeLLM, LLMError, LLMResponse, Message
from radio_llama.models import Show, ShowConfig
from radio_llama.music import JamendoError, JamendoSource, LocalCatalog
from radio_llama.trace import traced_generate, tracing
from radio_llama.voice import CachedTextToSpeech, FakeTTS, Voice
from tests.conftest import FakeFactServices, FakeJamendo

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

CANNED = "This is a fake radio script."
MESSAGES = [Message(role="system", content="Be brief."), Message(role="user", content="Hello")]
VOICE = Voice(voice_id="v1", model_id="m1", settings={"stability": 0.5})


class FailingLLM:
    provider = "failing"

    def generate(
        self, messages: list[Message], *, max_tokens: int, temperature: float
    ) -> LLMResponse:
        raise LLMError("Fake provider: the model is out today")


def _records(folder: Path, service: str) -> list[dict]:
    text = (folder / f"{service}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def _show(**config) -> Show:
    return Show(
        id="test-show",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", **config),
    )


def _generator(
    settings: Settings,
    catalog: LocalCatalog,
    llm: FakeLLM | None = None,
    jamendo: FakeJamendo | None = None,
    facts: FakeFactServices | None = None,
) -> PipelineGenerator:
    tts = CachedTextToSpeech(FakeTTS(settings.cache_dir / "tts-raw"), settings.cache_dir)
    sources = {"catalog": catalog}
    if jamendo is not None:
        sources["jamendo"] = jamendo.source
    lookup = facts.lookup if facts is not None else None
    return PipelineGenerator(llm or FakeLLM([CANNED]), tts, sources, settings, facts=lookup)


# The record writer


def test_nothing_is_written_without_a_current_trace(tmp_path: Path) -> None:
    assert trace.current() is None
    trace.record("llm", 0.0, label="x")
    traced_generate(FakeLLM(), MESSAGES, label="x", max_tokens=10, temperature=0.5)
    assert list(tmp_path.iterdir()) == []


def test_tracing_sets_and_resets_the_current_folder(tmp_path: Path) -> None:
    with tracing(tmp_path / "trace") as folder:
        assert trace.current() == folder
        trace.record("llm", 0.0, label="x")
    assert trace.current() is None
    assert [path.name for path in (tmp_path / "trace").iterdir()] == ["llm.jsonl"]


def test_a_record_has_the_common_fields_then_the_call(tmp_path: Path) -> None:
    with tracing(tmp_path):
        trace.record("tts", 0.0, text="Bonjour à tous", cached=True)

    (record,) = _records(tmp_path, "tts")
    assert list(record) == ["ts", "latency_s", "error", "text", "cached"]
    assert datetime.fromisoformat(record["ts"]).tzinfo is not None
    assert record["latency_s"] > 0
    assert record["error"] is None
    assert record["text"] == "Bonjour à tous"  # not escaped
    assert "Bonjour à tous" in (tmp_path / "tts.jsonl").read_text(encoding="utf-8")


def test_a_secret_is_redacted_from_the_whole_line(tmp_path: Path) -> None:
    with tracing(tmp_path):
        trace.record("jamendo", 0.0, redact="s3cret", url="https://x/?from=app-s3cret")

    (record,) = _records(tmp_path, "jamendo")
    assert record["url"] == "https://x/?from=app-<redacted>"
    assert "s3cret" not in (tmp_path / "jamendo.jsonl").read_text(encoding="utf-8")


def test_a_record_that_cannot_be_written_is_logged_and_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    with tracing(blocked / "trace"):
        trace.record("llm", 0.0, label="x")  # the folder cannot be created
    with tracing(tmp_path / "trace"):
        trace.record("tts", 0.0, odd={1, 2})  # a set is not JSON

    assert not (blocked / "trace").exists()
    assert not (tmp_path / "trace").exists()
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 2
    assert warnings[0].startswith("Trace record for llm not written")
    assert warnings[1].startswith("Trace record for tts not written")


# LLM


def test_llm_record_holds_the_messages_and_the_answer(tmp_path: Path) -> None:
    llm = FakeLLM(["Good evening."])
    with tracing(tmp_path):
        response = traced_generate(llm, MESSAGES, label="intro", max_tokens=2000, temperature=0.8)

    (record,) = _records(tmp_path, "llm")
    assert record["error"] is None
    assert record["label"] == "intro"
    assert record["provider"] == "fake"
    assert record["model"] == "fake"
    assert record["max_tokens"] == 2000
    assert record["temperature"] == 0.8
    assert record["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Hello"},
    ]
    assert record["text"] == "Good evening." == response.text
    assert record["input_tokens"] == response.input_tokens
    assert record["output_tokens"] == response.output_tokens


def test_a_failed_llm_call_leaves_an_error_record(tmp_path: Path) -> None:
    with tracing(tmp_path), pytest.raises(LLMError, match="out today"):
        traced_generate(FailingLLM(), MESSAGES, label="outro", max_tokens=10, temperature=0.5)

    (record,) = _records(tmp_path, "llm")
    assert record["error"] == "Fake provider: the model is out today"
    assert record["label"] == "outro"
    assert record["provider"] == "failing"
    assert record["messages"][1]["content"] == "Hello"
    assert record["text"] is None
    assert record["model"] is None


# TTS


def test_tts_records_a_miss_then_a_hit(tmp_path: Path) -> None:
    tts = CachedTextToSpeech(FakeTTS(tmp_path / "raw"), tmp_path / "cache")
    with tracing(tmp_path / "trace"):
        path = tts.synthesize("hello", VOICE)
        tts.synthesize("hello", VOICE)

    miss, hit = _records(tmp_path / "trace", "tts")
    assert miss["cached"] is False
    assert hit["cached"] is True
    for record in (miss, hit):
        assert record["error"] is None
        assert record["text"] == "hello"
        assert record["voice_id"] == "v1"
        assert record["model_id"] == "m1"
        assert record["voice_settings"] == {"stability": 0.5}
        assert record["path"] == str(path)
        assert record["bytes"] == path.stat().st_size > 0
    assert miss["latency_s"] > hit["latency_s"]


# Jamendo


def test_jamendo_records_carry_no_client_id(fake_jamendo: FakeJamendo, tmp_path: Path) -> None:
    with tracing(tmp_path):
        tracks = fake_jamendo.source.list_tracks("jazz")
        fake_jamendo.source.audio_path(tracks[0])

    # The real API echoes the client id in every audio URL (`from=app-<client_id>`), and
    # the fixture does the same.
    assert "from=app-test-client-id" in fake_jamendo.source._stream_urls["jamendo-1001"]
    text = (tmp_path / "jamendo.jsonl").read_text(encoding="utf-8")
    assert "test-client-id" not in text
    assert "client_id" not in text
    search, artists, download = _records(tmp_path, "jamendo")
    assert search["method"] == "tracks"
    assert search["params"]["tags"] == "jazz"
    assert search["params"]["format"] == "json"
    assert search["status"] == 200
    assert search["body"]["results"][0]["name"] == "Blue Window"
    assert search["body"]["results"][0]["audio"].endswith("from=app-<redacted>")
    assert artists["method"] == "artists/musicinfo"
    assert download["method"] == "download"
    assert download["url"].startswith("https://prod-1.storage.jamendo.com/")
    assert download["url"].endswith("from=app-<redacted>")
    assert download["path"].endswith("jamendo-1001.mp3")
    assert download["bytes"] == Path(download["path"]).stat().st_size > 0
    assert "body" not in download  # never the audio


def test_a_jamendo_api_error_is_recorded(fake_jamendo: FakeJamendo, tmp_path: Path) -> None:
    fake_jamendo.api_error = (6, "Rate Limit Exceeded")
    with tracing(tmp_path), pytest.raises(Exception, match="rate limit"):
        fake_jamendo.source.list_tracks("jazz")

    (record,) = _records(tmp_path, "jamendo")
    assert "rate limit" in record["error"]
    assert record["body"]["headers"]["code"] == 6


def test_a_jamendo_reply_that_is_not_an_object_is_recorded_as_an_error(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=["odd"]))
    source = JamendoSource(
        "test-client-id", tmp_path / "cache", http=httpx.Client(transport=transport)
    )
    with tracing(tmp_path), pytest.raises(JamendoError, match="other than JSON"):
        source.list_tracks("jazz")

    (record,) = _records(tmp_path, "jamendo")
    assert "other than JSON" in record["error"]
    assert record["status"] == 200
    assert record["body"] == ["odd"]


def test_a_failed_audio_write_is_recorded(fake_jamendo: FakeJamendo, tmp_path: Path) -> None:
    tracks = fake_jamendo.source.list_tracks("jazz")
    # The audio folder cannot be created: a file sits where it should go.
    (fake_jamendo.source._dir.parent).mkdir(parents=True, exist_ok=True)
    fake_jamendo.source._dir.write_text("in the way", encoding="utf-8")
    with tracing(tmp_path), pytest.raises(OSError):
        fake_jamendo.source.audio_path(tracks[0])

    download = _records(tmp_path, "jamendo")[-1]
    assert download["method"] == "download"
    assert download["error"]
    assert download["bytes"] is None


# Facts


def test_facts_services_get_one_file_each_and_a_cache_line(
    fake_facts: FakeFactServices, tmp_path: Path
) -> None:
    with tracing(tmp_path):
        found = fake_facts.lookup.artist_facts("Kevin MacLeod", "en")
        fake_facts.lookup.artist_facts("Kevin MacLeod", "en")  # memory
        fake_facts.new_lookup().artist_facts("Kevin MacLeod", "en")  # disk

    search, artist = _records(tmp_path, "musicbrainz")
    assert search["url"] == "https://musicbrainz.org/ws/2/artist/"
    assert search["params"]["query"] == 'artist:"Kevin MacLeod"'
    assert search["attempt"] == 1
    assert search["status"] == 200
    assert search["body"]["artists"][0]["name"] == "Kevin MacLeod"
    assert artist["params"] == {"inc": "url-rels", "fmt": "json"}
    (wikidata,) = _records(tmp_path, "wikidata")
    assert wikidata["url"].endswith("/Q16731782.json")
    assert wikidata["params"] is None
    assert "entities" in wikidata["body"]
    (wikipedia,) = _records(tmp_path, "wikipedia")
    assert wikipedia["url"].startswith("https://en.wikipedia.org/")
    assert wikipedia["body"]["type"] == "standard"
    memory, disk = _records(tmp_path, "facts")
    assert memory["cached"] == "memory"
    assert disk["cached"] == "disk"
    for record in (memory, disk):
        assert record["artist"] == "Kevin MacLeod"
        assert record["language"] == "en"
        assert [fact["text"] for fact in record["facts"]] == [fact.text for fact in found]


def test_a_musicbrainz_retry_is_two_records(fake_facts: FakeFactServices, tmp_path: Path) -> None:
    fake_facts.errors["musicbrainz_artist_search"] = 503
    with tracing(tmp_path):
        fake_facts.lookup.artist_facts("Kevin MacLeod", "en")

    first, second = _records(tmp_path, "musicbrainz")
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert first["error"] == second["error"] == "HTTP 503"
    assert first["status"] == 503
    assert first["body"] == {"error": "fake error"}
    assert not (tmp_path / "facts.jsonl").exists()


# Whole runs


def test_a_catalog_run_writes_the_trace_under_the_show(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    out_dir = tmp_path / "out"
    show = run_generation(_generator(fake_settings, tiny_catalog), _show(seed=1), out_dir)

    assert show.status == "ready", show.error
    folder = out_dir / "trace"
    assert sorted(path.name for path in folder.iterdir()) == ["llm.jsonl", "tts.jsonl"]
    llm = _records(folder, "llm")
    assert [record["label"] for record in llm] == [
        "intro",
        f"transition before {show.tracks[1].display_title} ({show.segments[2].shape})",
        f"transition before {show.tracks[2].display_title} ({show.segments[4].shape})",
        "outro",
    ]
    assert show.tracks[0].display_title in llm[0]["messages"][1]["content"]
    assert all(record["text"] == CANNED for record in llm)
    tts = _records(folder, "tts")
    assert [record["cached"] for record in tts] == [False, True, True, True]  # one canned line
    assert trace.current() is None


def test_a_jamendo_run_writes_one_file_per_service(
    fake_settings: Settings,
    tiny_catalog: LocalCatalog,
    fake_jamendo: FakeJamendo,
    fake_facts: FakeFactServices,
    tmp_path: Path,
) -> None:
    hit = fake_facts.replies["musicbrainz_artist_search"]["artists"][0]
    hit["aliases"] = [{"name": "Example Band"}, {"name": "Lea Example"}, {"name": "We Are FM"}]
    generator = _generator(fake_settings, tiny_catalog, jamendo=fake_jamendo, facts=fake_facts)
    out_dir = tmp_path / "out"

    show = run_generation(generator, _show(source="jamendo", seed=1), out_dir)

    assert show.status == "ready", show.error
    names = sorted(path.name for path in (out_dir / "trace").iterdir())
    assert names == [
        "facts.jsonl",
        "jamendo.jsonl",
        "llm.jsonl",
        "musicbrainz.jsonl",
        "tts.jsonl",
        "wikidata.jsonl",
        "wikipedia.jsonl",
    ]
    downloads = [r for r in _records(out_dir / "trace", "jamendo") if r["method"] == "download"]
    assert len(downloads) == 3
    # Two picked tracks share an artist: the second lookup is a memory hit.
    artists = [track.artist for track in show.tracks]
    assert len(set(artists)) == 2
    (cached,) = _records(out_dir / "trace", "facts")
    assert cached["cached"] == "memory"
    assert cached["artist"] in artists


def test_a_failed_run_keeps_its_trace(
    fake_settings: Settings, tiny_catalog: LocalCatalog, tmp_path: Path
) -> None:
    llm = FakeLLM([CANNED, ""])  # the second script is empty: an LLMError
    out_dir = tmp_path / "out"

    show = run_generation(_generator(fake_settings, tiny_catalog, llm), _show(seed=1), out_dir)

    assert show.status == "failed"
    assert len(_records(out_dir / "trace", "llm")) == 2
    assert trace.current() is None


def test_write_script_traces_under_the_scripts_folder(
    fake_settings: Settings, tiny_catalog: LocalCatalog
) -> None:
    show = _generator(fake_settings, tiny_catalog).write_script(_show(seed=1))

    folder = fake_settings.output_dir / "scripts" / show.id
    assert [path.name for path in folder.iterdir()] == ["llm.jsonl"]
    assert len(_records(folder, "llm")) == 4
    assert not (fake_settings.output_dir / show.id).exists()
