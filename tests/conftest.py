"""Shared fixtures: a tiny catalog with real (tone) audio and settings that use the fakes."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from radio_llama.config import Settings
from radio_llama.music import JamendoSource, LocalCatalog, jamendo

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
    `reject_params` gets an empty result list, which exercises the query relaxation.
    `empty_replies` is how many tracks calls answer an empty list before the real one,
    like the live API does now and then. `api_error` makes every API call answer that
    Jamendo error code.
    """

    def __init__(self, cache_dir: Path, tone: bytes) -> None:
        self.requests: list[httpx.Request] = []
        self.reject_params: set[str] = set()
        self.empty_replies = 0
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
            body = dict(self._tracks)
            if self.reject_params & set(params):
                body = {**body, "results": []}
            elif self.empty_replies > 0:
                self.empty_replies -= 1
                body = {**body, "results": []}
            elif "id" in params:
                wanted = params["id"].split()
                body = {**body, "results": [r for r in body["results"] if r["id"] in wanted]}
            return httpx.Response(200, json=body)
        if request.url.path == "/v3.0/artists/musicinfo/":
            wanted = params["id"].split()
            results = [r for r in self._artists["results"] if r["id"] in wanted]
            return httpx.Response(200, json={**self._artists, "results": results})
        return httpx.Response(404)


@pytest.fixture
def fake_jamendo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeJamendo:
    monkeypatch.setattr(jamendo, "RETRY_PAUSE_S", 0.0)
    tone_path = tmp_path / "jamendo-tone.mp3"
    _tone(tone_path, 330.0, TONE_SECONDS)
    return FakeJamendo(tmp_path / "cache", tone_path.read_bytes())
