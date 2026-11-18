"""Shared fixtures: a tiny catalog with real (tone) audio, settings that use the fakes, and
the remote services (Jamendo, YouTube, MusicBrainz, Wikidata, Wikipedia) on mock
transports."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from elevenlabs.core.api_error import ApiError

from radio_llama import facts
from radio_llama.config import Settings
from radio_llama.facts import FactLookup
from radio_llama.generator import factory
from radio_llama.music import JamendoSource, LocalCatalog, YouTubeSource, jamendo
from radio_llama.voice import ElevenLabsVoices

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
TONE_SECONDS = 3.0


def _track(track_id: str, style: str, number: int) -> dict:
    return {
        "id": track_id,
        "title": f"Title {track_id}",
        "artist": f"Test Artist {number}",
        "style": style,
        "album": "Test Album",
        "year": 2020,
        "duration_s": TONE_SECONDS,
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "track_url": f"https://example.com/{track_id}",
        "artist_url": f"https://example.com/artist-{number}",
        "audio_file": f"{track_id}.mp3",
        "tags": ["test"],
        "facts": [{"text": f"Fact about {track_id}.", "source_url": "https://example.com"}],
        "notes": "Curator notes that must never leave the catalog.",
    }


def _tone(path: Path, frequency: float, seconds: float) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(path),
        ],
        check=True,
    )


@pytest.fixture
def tiny_catalog(tmp_path: Path) -> LocalCatalog:
    """Two styles (jazz, rock), three tracks each, with a 3 s tone per track."""
    tracks = [
        _track(f"{style}-{number}", style, number)
        for style in ("jazz", "rock")
        for number in (1, 2, 3)
    ]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"version": 1, "tracks": tracks}), encoding="utf-8")
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    for index, track in enumerate(tracks):
        _tone(audio_dir / track["audio_file"], 220.0 * (index + 1), TONE_SECONDS)
    return LocalCatalog(catalog_path)


@pytest.fixture
def fake_settings(tmp_path: Path, tiny_catalog: LocalCatalog) -> Settings:
    return Settings(
        _env_file=None,
        llm_provider="fake",
        tts_backend="fake",
        cache_dir=tmp_path / "cache",
        output_dir=tmp_path / "output",
        catalog_path=tiny_catalog.catalog_path,
        prompts_dir=PROMPTS_DIR,
    )


JAMENDO_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jamendo"


class FakeJamendo:
    """A JamendoSource on a mock transport: recorded JSON for the API, a tone for the audio.

    `requests` records every request. A request carrying any parameter named in
    `reject_params` gets an empty result list, like a query that matches nothing, which
    exercises the query relaxation. `empty_replies` is how many tracks calls answer an
    empty list before the real one, like the live API does now and then: a false empty
    reply. `api_error` makes every API call answer that Jamendo error code. A search is
    answered one page at a time, by its `limit` and `offset`; `ignore_offset` answers
    the first page whatever the offset, like a search that never ends.

    A search sent with `fullcount=true` is answered like the live API (measured on
    2026-11-18, D044): the reply says how many results the whole query has in
    `results_fullcount`, a string, also past the last page and for a query that matches
    nothing. A false empty reply does not say it. A full page with more after it carries
    a `next` header, a URL that echoes the client id.
    """

    def __init__(self, cache_dir: Path, tone: bytes) -> None:
        self.requests: list[httpx.Request] = []
        self.reject_params: set[str] = set()
        self.empty_replies = 0
        self.ignore_offset = False
        self.api_error: tuple[int, str] | None = None
        self._tone = tone
        self._tracks = json.loads((JAMENDO_FIXTURES / "tracks.json").read_text(encoding="utf-8"))
        self._artists = json.loads(
            (JAMENDO_FIXTURES / "artists_musicinfo.json").read_text(encoding="utf-8")
        )
        client = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.source = JamendoSource("test-client-id", cache_dir, http=client)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "prod-1.storage.jamendo.com":
            return httpx.Response(200, content=self._tone)
        params = request.url.params
        if self.api_error is not None:
            code, message = self.api_error
            headers = {"status": "failed", "code": code, "error_message": message}
            return httpx.Response(200, json={"headers": headers, "results": []})
        if request.url.path == "/v3.0/tracks/":
            headers = dict(self._tracks["headers"])
            results = self._tracks["results"]
            total: int | None = None  # the results of the whole query; None: not said
            if self.reject_params & set(params):
                results, total = [], 0  # a query that matches nothing
            elif self.empty_replies > 0:
                self.empty_replies -= 1
                results = []  # a false empty reply: no results and no total
            elif "id" in params:
                wanted = params["id"].split()
                results = [r for r in results if r["id"] in wanted]
            elif self.ignore_offset:
                total = 1_000_000  # the first page again and again
            else:
                # One page of the search: `limit` results from `offset` on. The fixture
                # holds four tracks, so a show that asks for more runs out (D044).
                start, limit = int(params.get("offset", "0")), int(params["limit"])
                total = len(results)
                results = results[start : start + limit]
                if start + limit < total and "fullcount" in params:
                    headers["next"] = str(request.url.copy_set_param("offset", start + limit))
            headers["results_count"] = len(results)
            if "fullcount" in params and total is not None:
                headers["results_fullcount"] = str(total)
            return httpx.Response(200, json={"headers": headers, "results": results})
        if request.url.path == "/v3.0/artists/musicinfo/":
            wanted = params["id"].split()
            results = [r for r in self._artists["results"] if r["id"] in wanted]
            return httpx.Response(200, json={**self._artists, "results": results})
        return httpx.Response(404)


@pytest.fixture
def fake_jamendo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeJamendo:
    monkeypatch.setattr(jamendo, "RETRY_PAUSE_S", 0.0)
    monkeypatch.setattr(jamendo, "FALSE_EMPTY_PAUSE_S", 0.0)
    tone_path = tmp_path / "jamendo-tone.mp3"
    _tone(tone_path, 330.0, TONE_SECONDS)
    return FakeJamendo(tmp_path / "cache", tone_path.read_bytes())


YOUTUBE_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "youtube"

# Which videos of search.json each query returns, by the song title inside the query. The
# Blue Window search returns every kind of unplayable video before the artist's own upload;
# "broken song" returns unplayable videos only. A query that matches nothing returns nothing.
# The fake ignores maxResults so one search can exercise every keep rule.
SEARCH_RESULTS = {
    "blue window": [
        "okPlain0001",
        "noEmbed0001",
        "liveNow0001",
        "tooLong0001",
        "ageRestr001",
        "blockedFR01",
        "okTopic0001",
    ],
    "montreal streets": ["montreal001", "slowRivr001"],
    "night train": ["slowRivr001", "nightTrn001"],
    "red lights": ["redLight001"],
    "slow river": ["slowRivr001"],
    "broken song": ["noEmbed0001", "liveNow0001"],
}
QUOTA_ERROR = {
    "error": {
        "code": 403,
        "message": "The request cannot be completed because you have exceeded your quota.",
        "errors": [
            {
                "message": "The request cannot be completed because you have exceeded your quota.",
                "domain": "youtube.quota",
                "reason": "quotaExceeded",
            }
        ],
    }
}


class FakeYouTube:
    """A YouTubeSource on a mock transport answering recorded JSON (D038).

    `search.json` holds every search result and `videos.json` the matching `videos.list`
    items; SEARCH_RESULTS says which results a query gets. `requests` records every
    request. `quota_exceeded` makes every search answer the 403 the live API sends once
    the daily search quota is used up.
    """

    def __init__(self, cache_dir: Path, region: str = "FR") -> None:
        self.requests: list[httpx.Request] = []
        self.quota_exceeded = False
        self._search = json.loads((YOUTUBE_FIXTURES / "search.json").read_text(encoding="utf-8"))
        self._videos = json.loads((YOUTUBE_FIXTURES / "videos.json").read_text(encoding="utf-8"))
        client = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.source = YouTubeSource("test-api-key", cache_dir, region, http=client)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        params = request.url.params
        if request.url.path == "/youtube/v3/search":
            if self.quota_exceeded:
                return httpx.Response(403, json=QUOTA_ERROR)
            query = params.get("q", "").casefold()
            wanted = next((ids for title, ids in SEARCH_RESULTS.items() if title in query), [])
            by_id = {item["id"]["videoId"]: item for item in self._search["items"]}
            items = [by_id[video_id] for video_id in wanted]
            return httpx.Response(200, json={**self._search, "items": items})
        if request.url.path == "/youtube/v3/videos":
            wanted = params.get("id", "").split(",")
            items = [item for item in self._videos["items"] if item["id"] in wanted]
            return httpx.Response(200, json={**self._videos, "items": items})
        return httpx.Response(404)

    def searches(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/youtube/v3/search"]


@pytest.fixture
def fake_youtube(tmp_path: Path) -> FakeYouTube:
    return FakeYouTube(tmp_path / "cache")


class RefusingVoiceSearch:
    def search(self, **kwargs: object) -> None:
        raise ApiError(
            status_code=401,
            body={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}},
        )


class RefusingElevenLabs:
    """Stands in for the ElevenLabs client: its voice search answers 401 (D040)."""

    voices = RefusingVoiceSearch()


def refused_voices() -> ElevenLabsVoices:
    """The real voice list on a client ElevenLabs refuses, for the error path end to end."""
    return ElevenLabsVoices("test-key", "test-model", client=RefusingElevenLabs())  # type: ignore[arg-type]


FACTS_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "facts"


class FakeFactServices:
    """A FactLookup on a mock transport that answers recorded JSON for Kevin MacLeod.

    The replies were recorded on 2026-11-11 and trimmed. Every artist search gets the same
    reply. `replies` maps a route (a fixture file name) to the JSON it answers; tests edit
    it. `errors` maps a route to an HTTP status or an exception to raise instead, or to a
    list of them, used up one per request. `requests` records every request.
    `new_lookup()` gives another FactLookup on the same services and cache, like a later
    show.
    """

    def __init__(self, cache_dir: Path) -> None:
        self.requests: list[httpx.Request] = []
        self.replies: dict[str, dict] = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in FACTS_FIXTURES.glob("*.json")
        }
        self.errors: dict[str, int | Exception | list[int | Exception]] = {}
        self._cache_dir = cache_dir
        self._client = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.lookup = self.new_lookup()

    def new_lookup(self) -> FactLookup:
        return FactLookup(self._cache_dir, http=self._client)

    @staticmethod
    def route(request: httpx.Request) -> str:
        host, path = request.url.host, request.url.path
        if host == "musicbrainz.org":
            return "musicbrainz_artist_search" if path == "/ws/2/artist/" else "musicbrainz_artist"
        if host == "www.wikidata.org":
            return "wikidata_entity"
        if host.endswith(".wikipedia.org"):
            return f"wikipedia_summary_{host.split('.')[0]}"
        return "unknown"

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.route(request)
        error = self.errors.get(route)
        if isinstance(error, list):
            error = error.pop(0) if error else None
        if isinstance(error, Exception):
            raise error
        if error is not None:
            return httpx.Response(error, json={"error": "fake error"})
        if route not in self.replies:
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=self.replies[route])

    def routes(self) -> list[str]:
        return [self.route(request) for request in self.requests]


@pytest.fixture
def fake_facts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeFactServices:
    monkeypatch.setattr(facts, "MUSICBRAINZ_PAUSE_S", 0.0)
    return FakeFactServices(tmp_path / "cache")


@pytest.fixture(autouse=True)
def offline_fact_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`make_generator` builds a real FactLookup. In tests it gets a transport that answers
    404 to everything, so no test reaches MusicBrainz or Wikipedia."""

    def offline(cache_dir: Path) -> FactLookup:
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        return FactLookup(cache_dir, http=httpx.Client(transport=transport))

    monkeypatch.setattr(factory, "FactLookup", offline)
