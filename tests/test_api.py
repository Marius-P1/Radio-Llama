"""Tests for the `/api/v1` routes with the fake LLM and fake TTS.

FastAPI's TestClient runs background tasks before the response reaches the test, so a
POST /shows here completes the whole generation before `client.post` returns. The
`pending` status is still visible in the POST body because it is built before the task.
"""

import json
import logging
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from radio_llama.api import jobs as jobs_module
from radio_llama.api.app import create_app
from radio_llama.api.jobs import Jobs
from radio_llama.api.routes import next_part
from radio_llama.api.schemas import NextPartRequest, ShowResponse
from radio_llama.cli import app as cli_app
from radio_llama.config import Settings
from radio_llama.generator.pipeline import END_NOTE_NO_SONGS
from radio_llama.llm import FakeLLM
from radio_llama.models import Segment, Show, ShowConfig
from radio_llama.music import LocalCatalog, playback_of
from radio_llama.personas import get_persona, load_personas
from radio_llama.storage import load_show, save_show
from radio_llama.voice import TTSError
from tests.conftest import PROMPTS_DIR, FakeJamendo, refused_voices

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

PERSONA_IDS = ["calm", "energetic", "witty"]


class FailingGenerator:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def generate(self, show: Show, out_dir: Path, on_progress=None) -> Show:
        raise self._exc


@pytest.fixture
def client(fake_settings: Settings) -> TestClient:
    with TestClient(create_app(fake_settings)) as client:
        yield client


def _create(client: TestClient, **body) -> dict:
    response = client.post("/api/v1/shows", json={"style": "jazz", "persona": "calm", **body})
    assert response.status_code == 202, response.text
    return response.json()


def test_options_lists_sources_personas_and_languages(client: TestClient) -> None:
    response = client.get("/api/v1/options")

    assert response.status_code == 200
    body = response.json()
    assert body["sources"] == [
        {
            "id": "catalog",
            "name": "Local catalog",
            "styles": ["jazz", "rock"],
            "wish": False,
            "endless": False,
        }
    ]
    assert body["languages"] == ["en", "fr"]
    assert body["energies"] == ["calm", "medium", "upbeat"]
    assert sorted(body["personas"]) == ["en", "fr"]
    for language in ("en", "fr"):
        assert [persona["id"] for persona in body["personas"][language]] == PERSONA_IDS
        assert all(
            persona["name"] and persona["description"] for persona in body["personas"][language]
        )


def test_create_show_then_read_it_and_its_audio(client: TestClient) -> None:
    created = _create(client)
    assert created["status"] == "pending"
    assert created["config"]["style"] == "jazz"

    response = client.get(f"/api/v1/shows/{created['id']}")
    assert response.status_code == 200
    show = response.json()
    assert show["status"] == "ready"
    assert show["error"] is None
    assert show["progress"] is None
    assert len(show["segments"]) == 7
    assert len(show["tracks"]) == 3
    assert show["audio_url"] == f"/api/v1/shows/{created['id']}/audio"
    assert show["playback"] == "file"
    # Every speech segment can be fetched on its own too (D038); songs cannot.
    for index, segment in enumerate(show["segments"]):
        if segment["kind"] == "song":
            assert segment["audio_url"] is None
        else:
            assert segment["audio_url"] == (f"/api/v1/shows/{created['id']}/segments/{index}/audio")
    for track in show["tracks"]:
        assert track["license"] and track["license_url"]
        assert track["track_url"] and track["artist_url"]
        assert "notes" not in track

    audio = client.get(show["audio_url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/mpeg")
    assert audio.content.startswith(b"ID3") or audio.content.startswith(b"\xff")


def test_segment_audio_of_a_catalog_show_serves_speech_only(client: TestClient) -> None:
    """A file show keeps its songs inside show.mp3: the segment route never serves one,
    even though the song segment points at its audio file."""
    created = _create(client)
    show = client.get(f"/api/v1/shows/{created['id']}").json()
    assert show["segments"][1]["kind"] == "song"
    assert show["segments"][1]["audio_file"] is not None

    base = f"/api/v1/shows/{created['id']}/segments"
    speech = client.get(f"{base}/0/audio")
    assert speech.status_code == 200
    assert speech.headers["content-type"].startswith("audio/mpeg")
    assert client.get(f"{base}/1/audio").status_code == 404


def test_list_shows_newest_first(client: TestClient) -> None:
    first = _create(client)
    second = _create(client, style="rock")

    response = client.get("/api/v1/shows")

    assert response.status_code == 200
    ids = [show["id"] for show in response.json()]
    assert ids.index(second["id"]) < ids.index(first["id"])
    assert all(show["status"] == "ready" for show in response.json())
    assert "segments" not in response.json()[0]


def test_list_shows_ignores_folders_without_a_show_file(
    client: TestClient, fake_settings: Settings
) -> None:
    """`radio-llama script` leaves its trace under output/scripts/ (D037)."""
    scripts = fake_settings.output_dir / "scripts" / "20261030-120000-abcdef"
    scripts.mkdir(parents=True)
    (scripts / "llm.jsonl").write_text("{}\n", encoding="utf-8")
    created = _create(client)

    response = client.get("/api/v1/shows")

    assert response.status_code == 200
    assert [show["id"] for show in response.json()] == [created["id"]]


def test_energy_is_stored_in_the_config(client: TestClient) -> None:
    created = _create(client, energy="calm")

    assert created["config"]["energy"] == "calm"
    assert client.get(f"/api/v1/shows/{created['id']}").json()["config"]["energy"] == "calm"
    assert _create(client)["config"]["energy"] is None
    assert _create(client, energy=None)["config"]["energy"] is None


def test_demo_is_stored_in_the_config(client: TestClient) -> None:
    created = _create(client, demo=True)

    assert created["config"]["demo"] is True
    assert client.get(f"/api/v1/shows/{created['id']}").json()["config"]["demo"] is True
    assert _create(client)["config"]["demo"] is False  # full songs by default (D023)


def test_unknown_energy_is_a_422(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "energy": "frantic"})

    assert response.status_code == 422


def test_unknown_style_is_a_400(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "polka"})

    assert response.status_code == 400
    assert "Known styles" in response.json()["detail"]
    assert "jazz" in response.json()["detail"]


def test_unknown_persona_is_a_400(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "persona": "grumpy"})

    assert response.status_code == 400
    assert "calm" in response.json()["detail"]


def test_unknown_generator_is_a_422(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "generator": "agent"})

    assert response.status_code == 422


def test_unknown_show_is_a_404(client: TestClient) -> None:
    response = client.get("/api/v1/shows/20261030-120000-abcdef")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_invalid_show_id_is_a_404_too(client: TestClient) -> None:
    assert client.get("/api/v1/shows/not-an-id").status_code == 404
    assert client.get("/api/v1/shows/not-an-id/audio").status_code == 404


def test_expected_generation_error_marks_the_show_failed(client: TestClient) -> None:
    client.app.state.jobs._generator = FailingGenerator(TTSError("quota"))

    created = _create(client)
    show = client.get(f"/api/v1/shows/{created['id']}").json()

    assert show["status"] == "failed"
    assert "quota" in show["error"]
    assert show["audio_url"] is None
    assert show["segments"] == []
    assert client.get(f"/api/v1/shows/{created['id']}/audio").status_code == 404
    assert client.get("/api/v1/options").status_code == 200


def test_unexpected_error_marks_the_show_failed_and_keeps_the_server_alive(
    client: TestClient,
) -> None:
    client.app.state.jobs._generator = FailingGenerator(RuntimeError("bug"))

    created = _create(client)
    show = client.get(f"/api/v1/shows/{created['id']}").json()

    assert show["status"] == "failed"
    assert show["error"] == "Unexpected error, see server log."
    assert client.get("/api/v1/options").status_code == 200


def test_startup_marks_unfinished_shows_failed(fake_settings: Settings) -> None:
    stale = Show(
        id="20261030-120000-abcdef",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm"),
        status="generating",
    )
    save_show(stale, fake_settings.output_dir / stale.id)

    with TestClient(create_app(fake_settings)) as client:
        show = client.get(f"/api/v1/shows/{stale.id}").json()

    assert show["status"] == "failed"
    assert "restarted" in show["error"]


def test_cors_allows_the_vite_dev_server(client: TestClient) -> None:
    response = client.get("/api/v1/options", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_export_openapi_writes_the_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tiny_catalog: LocalCatalog
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("CATALOG_PATH", str(tiny_catalog.catalog_path))
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PROMPTS_DIR", str(Path(__file__).resolve().parent.parent / "prompts"))
    target = tmp_path / "frontend" / "openapi.json"

    result = CliRunner().invoke(cli_app, ["export-openapi", str(target)])

    assert result.exit_code == 0, result.output
    schema = json.loads(target.read_text(encoding="utf-8"))
    assert "/api/v1/shows" in schema["paths"]
    assert "/api/v1/options" in schema["paths"]
    assert "/api/v1/voices" in schema["paths"]
    assert "/api/v1/shows/{show_id}/audio" in schema["paths"]
    assert "/api/v1/shows/{show_id}/segments/{index}/audio" in schema["paths"]
    assert schema["components"]["schemas"]["ShowResponse"]["properties"]["playback"] == {
        "enum": ["file", "segments"],
        "title": "Playback",
        "type": "string",
    }


def test_built_frontend_is_served_next_to_the_api(fake_settings: Settings, tmp_path: Path) -> None:
    tmp_dist = tmp_path / "dist"
    (tmp_dist / "assets").mkdir(parents=True)
    (tmp_dist / "index.html").write_text("<h1>Radio Llama test page</h1>", encoding="utf-8")
    (tmp_dist / "assets" / "app.js").write_text("console.log('hi')", encoding="utf-8")
    settings = fake_settings.model_copy(update={"frontend_dist": tmp_dist})

    with TestClient(create_app(settings)) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert "Radio Llama test page" in index.text
        assert client.get("/assets/app.js").text == "console.log('hi')"
        assert client.get("/api/v1/options").status_code == 200


def test_api_works_without_a_built_frontend(fake_settings: Settings, tmp_path: Path) -> None:
    settings = fake_settings.model_copy(update={"frontend_dist": tmp_path / "missing"})

    with TestClient(create_app(settings)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/v1/options").status_code == 200


# Music sources (D029, D030).


@pytest.fixture
def client_with_jamendo(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo
) -> TestClient:
    sources = {"catalog": tiny_catalog, "jamendo": fake_jamendo.source}
    with TestClient(create_app(fake_settings, sources)) as client:
        yield client


def test_options_list_jamendo_when_configured(client_with_jamendo: TestClient) -> None:
    body = client_with_jamendo.get("/api/v1/options").json()

    assert [source["id"] for source in body["sources"]] == ["catalog", "jamendo"]
    jamendo = body["sources"][1]
    assert jamendo["name"] == "Jamendo"
    assert jamendo["wish"] is True
    assert jamendo["endless"] is True  # D042: Jamendo and YouTube, not the catalog
    assert body["sources"][0]["endless"] is False
    assert {"jazz", "rock", "lofi", "hiphop"} <= set(jamendo["styles"])


def test_create_a_jamendo_show_with_a_wish(client_with_jamendo: TestClient) -> None:
    client_with_jamendo.app.state.jobs._generator._llm = FakeLLM(
        ['{"tags": ["piano"], "vocal": null, "lang": null, "search": null}', *(["Script."] * 4)]
    )

    created = _create(client_with_jamendo, source="jamendo", wish="  piano, please  ")
    show = client_with_jamendo.get(f"/api/v1/shows/{created['id']}").json()

    assert created["config"]["source"] == "jamendo"
    assert created["config"]["wish"] == "piano, please"
    assert show["status"] == "ready", show["error"]
    assert all(track["id"].startswith("jamendo-") for track in show["tracks"])
    assert all(
        track["track_url"].startswith("https://www.jamendo.com/") for track in show["tracks"]
    )


def test_blank_wish_is_no_wish(client_with_jamendo: TestClient) -> None:
    created = _create(client_with_jamendo, source="jamendo", wish="   ")

    assert created["config"]["wish"] is None


def test_catalog_show_defaults_stay(client: TestClient) -> None:
    created = _create(client)

    assert created["config"]["source"] == "catalog"
    assert created["config"]["wish"] is None


def test_unconfigured_source_is_a_400_naming_the_variable(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "source": "jamendo"})

    assert response.status_code == 400
    assert "JAMENDO_CLIENT_ID" in response.json()["detail"]


def test_unknown_source_is_a_422(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "source": "spotify"})

    assert response.status_code == 422


def test_wish_on_the_catalog_is_a_400(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "wish": "anything"})

    assert response.status_code == 400
    assert "jamendo" in response.json()["detail"]


def test_too_long_wish_is_a_422(client_with_jamendo: TestClient) -> None:
    response = client_with_jamendo.post(
        "/api/v1/shows", json={"style": "jazz", "source": "jamendo", "wish": "x" * 201}
    )

    assert response.status_code == 422


def test_style_is_checked_against_the_chosen_source(client_with_jamendo: TestClient) -> None:
    # "lofi" exists on Jamendo, not in the tiny catalog.
    assert client_with_jamendo.post("/api/v1/shows", json={"style": "lofi"}).status_code == 400
    response = client_with_jamendo.post(
        "/api/v1/shows", json={"style": "lofi", "source": "jamendo"}
    )
    assert response.status_code == 202


# YouTube shows (D038): no MP3, the segments play one by one.

PLAYLIST_JSON = (
    '{"songs": ['
    '{"artist": "Example Artist", "title": "Blue Window"}, '
    '{"artist": "Lea Example", "title": "Montreal Streets"}, '
    '{"artist": "We Are FM", "title": "Night Train"}, '
    '{"artist": "Example Records", "title": "Red Lights"}, '
    '{"artist": "River Uploads", "title": "Slow River"}'
    "]}"
)


@pytest.fixture
def client_with_youtube(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube
) -> TestClient:
    sources = {"catalog": tiny_catalog, "youtube": fake_youtube.source}
    with TestClient(create_app(fake_settings, sources)) as client:
        client.app.state.jobs._generator._llm = FakeLLM([PLAYLIST_JSON, *(["Script."] * 4)])
        yield client


def test_options_list_youtube_when_configured(client_with_youtube: TestClient) -> None:
    body = client_with_youtube.get("/api/v1/options").json()

    assert [source["id"] for source in body["sources"]] == ["catalog", "youtube"]
    # No style to pick on YouTube: the wish is the theme (D039).
    assert body["sources"][1] == {
        "id": "youtube",
        "name": "YouTube",
        "styles": [],
        "wish": True,
        "endless": True,
    }


def test_create_a_youtube_show_and_fetch_its_segments(client_with_youtube: TestClient) -> None:
    created = _create(client_with_youtube, style=None, source="youtube", wish="trains", demo=True)
    show = client_with_youtube.get(f"/api/v1/shows/{created['id']}").json()

    assert created["config"]["source"] == "youtube"
    assert created["config"]["wish"] == "trains"
    assert created["config"]["style"] is None
    assert show["config"]["style"] is None
    assert show["status"] == "ready", show["error"]
    assert show["playback"] == "segments"
    assert show["audio_url"] is None
    assert client_with_youtube.get(f"/api/v1/shows/{created['id']}/audio").status_code == 404
    assert [segment["kind"] for segment in show["segments"]] == [
        "intro",
        "song",
        "transition",
        "song",
        "transition",
        "song",
        "outro",
    ]
    for index, segment in enumerate(show["segments"]):
        if segment["kind"] == "song":
            assert segment["audio_url"] is None
            assert segment["audio_file"] is None
            assert segment["duration_s"] == 30.0
        else:
            assert segment["audio_url"] == f"/api/v1/shows/{created['id']}/segments/{index}/audio"
            assert segment["duration_s"] > 0
    for track in show["tracks"]:
        assert track["id"] == f"youtube-{track['video_id']}"
        assert track["audio_file"] is None
        assert track["track_url"] == f"https://www.youtube.com/watch?v={track['video_id']}"
        assert track["license"] == "YouTube embed"

    speech = client_with_youtube.get(show["segments"][0]["audio_url"])
    assert speech.status_code == 200
    assert speech.headers["content-type"].startswith("audio/mpeg")
    assert speech.content.startswith(b"ID3") or speech.content.startswith(b"\xff")
    base = f"/api/v1/shows/{created['id']}/segments"
    assert client_with_youtube.get(f"{base}/1/audio").status_code == 404  # a song
    assert client_with_youtube.get(f"{base}/7/audio").status_code == 404  # past the end
    assert client_with_youtube.get(f"{base}/-1/audio").status_code == 404
    assert client_with_youtube.get(f"{base}/intro/audio").status_code == 422
    assert client_with_youtube.get("/api/v1/shows/not-an-id/segments/0/audio").status_code == 404


def test_playback_comes_from_the_source_at_any_status() -> None:
    """A catalog show says "file" while it generates; a YouTube show says "segments" even
    before it is ready, so the page never needs the audio file to know the player."""

    def playback(source: str, status: str) -> str:
        show = Show(
            id="20261030-120000-abc123",
            created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
            config=ShowConfig(style="jazz", persona="calm", source=source),
            status=status,
        )
        return ShowResponse.from_show(show, None).playback

    assert playback("catalog", "generating") == "file"
    assert playback("jamendo", "failed") == "file"
    assert playback("youtube", "pending") == "segments"
    assert playback("youtube", "ready") == "segments"


def test_an_endless_show_plays_by_segments_whatever_its_source() -> None:
    """A show that grows cannot be one MP3 (D044): `playback` comes from the show."""
    fixed = ShowConfig(style="jazz", persona="calm", source="jamendo")
    endless = ShowConfig(style="jazz", persona="calm", source="jamendo", endless=True)
    youtube = ShowConfig(persona="calm", source="youtube", wish="trains", endless=True)

    assert playback_of(fixed) == "file"
    assert playback_of(endless) == "segments"
    assert playback_of(youtube) == "segments"
    show = Show(
        id="20261030-120000-abc123",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=endless,
        status="generating",
    )
    assert ShowResponse.from_show(show, None).playback == "segments"


def test_segment_audio_of_a_show_that_is_not_ready_is_a_404(client: TestClient) -> None:
    client.app.state.jobs._generator = FailingGenerator(TTSError("quota"))
    created = _create(client)

    assert client.get(f"/api/v1/shows/{created['id']}/segments/0/audio").status_code == 404


def test_unconfigured_youtube_is_a_400_naming_the_variable(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"source": "youtube", "wish": "trains"})

    assert response.status_code == 400
    assert "YOUTUBE_API_KEY" in response.json()["detail"]


# No style on YouTube (D039): the wish is the one required input.


def test_youtube_show_takes_no_style(client_with_youtube: TestClient) -> None:
    response = client_with_youtube.post(
        "/api/v1/shows", json={"style": "jazz", "source": "youtube", "wish": "trains"}
    )

    assert response.status_code == 400
    assert "'youtube'" in response.json()["detail"]
    assert "no styles" in response.json()["detail"]
    assert client_with_youtube.get("/api/v1/shows").json() == []


def test_youtube_show_needs_a_wish(client_with_youtube: TestClient) -> None:
    for body in ({"source": "youtube"}, {"source": "youtube", "wish": "   "}):
        response = client_with_youtube.post("/api/v1/shows", json=body)

        assert response.status_code == 400
        assert (
            response.json()["detail"] == "A YouTube show needs a wish: say what you want to hear."
        )
    assert client_with_youtube.get("/api/v1/shows").json() == []


def test_youtube_show_without_a_style_is_stored_with_a_null_style(
    client_with_youtube: TestClient,
) -> None:
    created = _create(client_with_youtube, style=None, source="youtube", wish="90s French rap")
    show = client_with_youtube.get(f"/api/v1/shows/{created['id']}").json()

    assert created["config"]["style"] is None
    assert show["status"] == "ready", show["error"]
    assert show["config"]["style"] is None
    assert [
        summary["config"]["style"] for summary in client_with_youtube.get("/api/v1/shows").json()
    ] == [None]
    # The key may also be left out of the request.
    response = client_with_youtube.post(
        "/api/v1/shows", json={"source": "youtube", "wish": "80s Italian disco"}
    )
    assert response.status_code == 202
    assert response.json()["config"]["style"] is None


def test_catalog_show_still_needs_a_style(client_with_youtube: TestClient) -> None:
    for body in ({"persona": "calm"}, {"style": None}):
        response = client_with_youtube.post("/api/v1/shows", json=body)

        assert response.status_code == 400
        assert "Known styles" in response.json()["detail"]


# The host's voice (D040): a list to pick from, checked when a show is created.


VOICES_REFUSED = "Could not list the ElevenLabs voices (HTTP 401)."


@pytest.fixture
def client_without_voices(fake_settings: Settings) -> TestClient:
    """The real ElevenLabs voice list on a client that answers 401 to every search."""
    with TestClient(create_app(fake_settings, voices=refused_voices())) as client:
        yield client


def test_options_give_each_persona_its_own_voice(client: TestClient) -> None:
    body = client.get("/api/v1/options").json()

    for language in ("en", "fr"):
        expected = [persona.voice_id for persona in load_personas(PROMPTS_DIR, language)]
        assert [persona["voice_id"] for persona in body["personas"][language]] == expected
        assert all(expected)


def test_voices_lists_the_voices_of_the_backend(client: TestClient) -> None:
    response = client.get("/api/v1/voices")

    assert response.status_code == 200
    voices = response.json()
    assert [voice["id"] for voice in voices] == [f"fake-voice-{n}" for n in range(1, 6)]
    assert voices[0] == {
        "id": "fake-voice-1",
        "name": "Ada",
        "gender": "female",
        "accent": "british",
        "age": "middle_aged",
        "descriptive": "warm",
        "language": "en",
        "languages": ["en", "fr"],
        "preview_url": None,
        "previews": {},
    }


def test_voices_failure_is_a_502_and_the_options_still_load(
    client_without_voices: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="radio_llama.voice.elevenlabs"):
        response = client_without_voices.get("/api/v1/voices")

    # The page gets a short sentence. What ElevenLabs answered is in the server log only.
    assert response.status_code == 502
    assert response.json() == {"detail": VOICES_REFUSED}
    assert "Invalid API key" in caplog.text
    assert client_without_voices.get("/api/v1/options").status_code == 200


def test_create_show_with_a_voice_saves_its_id_and_name(
    client: TestClient, fake_settings: Settings
) -> None:
    created = _create(client, voice_id="fake-voice-2")

    assert created["config"]["voice_id"] == "fake-voice-2"
    assert created["config"]["voice_name"] == "Basile"
    show_dir = fake_settings.output_dir / created["id"]
    saved = json.loads((show_dir / "show.json").read_text(encoding="utf-8"))
    assert saved["status"] == "ready", saved["error"]
    assert saved["config"]["voice_id"] == "fake-voice-2"
    assert saved["config"]["voice_name"] == "Basile"
    # The host spoke with that voice: every TTS call of the run names it (D037).
    lines = (show_dir / "trace" / "tts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert all(json.loads(line)["voice_id"] == "fake-voice-2" for line in lines)
    listed = client.get("/api/v1/shows").json()
    assert listed[0]["config"]["voice_name"] == "Basile"


def test_create_show_without_a_voice_keeps_the_personas(
    client: TestClient, fake_settings: Settings
) -> None:
    for body in ({}, {"voice_id": None}):
        created = _create(client, **body)

        assert created["config"]["voice_id"] is None
        assert created["config"]["voice_name"] is None
        show_dir = fake_settings.output_dir / created["id"]
        lines = (show_dir / "trace" / "tts.jsonl").read_text(encoding="utf-8").splitlines()
        calm = get_persona(PROMPTS_DIR, "en", "calm")
        assert all(json.loads(line)["voice_id"] == calm.voice_id for line in lines)


def test_unknown_voice_is_a_400(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "voice_id": "nobody"})

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unknown voice 'nobody'. Pick one of the listed voices, or leave the voice out "
        "to use the style's own voice."
    )
    assert client.get("/api/v1/shows").json() == []


def test_blank_voice_is_no_voice(client_without_voices: TestClient) -> None:
    """Like a blank wish. The list is not read, so it may even be failing."""
    for blank in ("", "   "):
        created = _create(client_without_voices, voice_id=blank)

        assert created["config"]["voice_id"] is None
        assert created["config"]["voice_name"] is None


def test_voice_id_with_spaces_around_is_found(client: TestClient) -> None:
    created = _create(client, voice_id="  fake-voice-2 ")

    assert created["config"]["voice_id"] == "fake-voice-2"
    assert created["config"]["voice_name"] == "Basile"


def test_too_long_voice_id_is_a_422(client: TestClient) -> None:
    """Refused by validation, before the voice list is read or an error text is built."""
    response = client.post("/api/v1/shows", json={"style": "jazz", "voice_id": "x" * 101})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "voice_id"]
    assert "at most 100 characters" in response.json()["detail"][0]["msg"]
    assert client.get("/api/v1/shows").json() == []
    # A hundred characters pass validation and reach the voice check.
    response = client.post("/api/v1/shows", json={"style": "jazz", "voice_id": "x" * 100})
    assert response.status_code == 400


def test_a_picked_voice_needs_the_list_and_no_voice_does_not(
    client_without_voices: TestClient,
) -> None:
    response = client_without_voices.post(
        "/api/v1/shows", json={"style": "jazz", "voice_id": "fake-voice-1"}
    )

    assert response.status_code == 502
    assert response.json() == {"detail": VOICES_REFUSED}
    assert client_without_voices.get("/api/v1/shows").json() == []
    # With no voice asked for, the list is not read: the show is created.
    assert _create(client_without_voices)["config"]["voice_id"] is None


# The show length (D041): the listener picks a number of songs, 1 to 10, 3 by default.


def _saved_show(settings: Settings, show_id: str) -> dict:
    return json.loads((settings.output_dir / show_id / "show.json").read_text(encoding="utf-8"))


def test_a_show_has_three_songs_by_default(client: TestClient, fake_settings: Settings) -> None:
    created = _create(client)

    assert created["config"]["track_count"] == 3
    saved = _saved_show(fake_settings, created["id"])
    assert saved["config"]["track_count"] == 3
    assert [segment["kind"] for segment in saved["segments"]].count("song") == 3


def test_a_chosen_song_count_reaches_the_show(client: TestClient, fake_settings: Settings) -> None:
    created = _create(client, track_count=2)

    assert created["config"]["track_count"] == 2
    saved = _saved_show(fake_settings, created["id"])
    assert saved["status"] == "ready", saved["error"]
    assert saved["config"]["track_count"] == 2
    assert [segment["kind"] for segment in saved["segments"]] == [
        "intro",
        "song",
        "transition",
        "song",
        "outro",
    ]
    assert len(saved["tracks"]) == 2
    show = client.get(f"/api/v1/shows/{created['id']}").json()
    assert show["config"]["track_count"] == 2
    assert len(show["segments"]) == 5


def test_a_youtube_show_can_have_more_songs_than_the_default(
    client_with_youtube: TestClient,
) -> None:
    # The playlist, then an intro, three breaks and an outro for four songs.
    client_with_youtube.app.state.jobs._generator._llm = FakeLLM(
        [PLAYLIST_JSON, *(["Script."] * 5)]
    )

    created = _create(
        client_with_youtube, style=None, source="youtube", wish="trains", track_count=4, demo=True
    )
    show = client_with_youtube.get(f"/api/v1/shows/{created['id']}").json()

    assert show["status"] == "ready", show["error"]
    assert show["config"]["track_count"] == 4
    assert len(show["tracks"]) == 4
    assert len(show["segments"]) == 9
    assert [segment["kind"] for segment in show["segments"]].count("song") == 4


def test_song_count_out_of_range_is_a_422(client: TestClient) -> None:
    for count in (0, 11, -1):
        response = client.post("/api/v1/shows", json={"style": "jazz", "track_count": count})

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "track_count"]
    assert client.get("/api/v1/shows").json() == []


def test_both_ends_of_the_song_count_range_are_accepted(client: TestClient) -> None:
    # One song: the shortest plan, with no break between songs.
    created = _create(client, track_count=1)
    show = client.get(f"/api/v1/shows/{created['id']}").json()

    assert created["config"]["track_count"] == 1
    assert show["status"] == "ready", show["error"]
    assert [segment["kind"] for segment in show["segments"]] == ["intro", "song", "outro"]
    assert len(show["tracks"]) == 1
    # Ten songs pass validation. The tiny catalog has three per style, so that show fails
    # later, like any count a source cannot fill.
    assert _create(client, track_count=10)["config"]["track_count"] == 10


def test_a_song_count_the_style_cannot_fill_gives_a_failed_show(client: TestClient) -> None:
    """The tiny catalog has three jazz tracks. No check at creation (D041): the show is
    accepted, then fails with a sentence the progress screen shows."""
    created = _create(client, track_count=5)
    show = client.get(f"/api/v1/shows/{created['id']}").json()

    assert created["status"] == "pending"
    assert show["status"] == "failed"
    assert show["error"] == "Style 'jazz' has 3 tracks, 5 needed."
    assert show["config"]["track_count"] == 5
    assert show["segments"] == []
    assert client.get("/api/v1/options").status_code == 200


def test_a_song_count_youtube_cannot_fill_names_the_source(
    client_with_youtube: TestClient,
) -> None:
    """A YouTube show has no style (D039). The playlist of the fake LLM holds five songs."""
    created = _create(
        client_with_youtube, style=None, source="youtube", wish="trains", track_count=10
    )
    show = client_with_youtube.get(f"/api/v1/shows/{created['id']}").json()

    assert show["status"] == "failed"
    assert show["error"] == "Source 'youtube' has 5 tracks, 10 needed."


# The endless show (D042, D044): POST /shows makes the opening, the player then asks for
# each next part with POST /shows/{id}/next and closes the show with POST /shows/{id}/end.


def _kinds(show: dict) -> list[str]:
    return [segment["kind"] for segment in show["segments"]]


def _next(client: TestClient, show_id: str, known: int):
    return client.post(f"/api/v1/shows/{show_id}/next", json={"known": known})


def _end(client: TestClient, show_id: str, after: int):
    return client.post(f"/api/v1/shows/{show_id}/end", json={"after": after})


def _count_llm_calls(client: TestClient) -> FakeLLM:
    """Give the app's generator a fake LLM whose calls the test can count."""
    llm = FakeLLM(["Script."])
    client.app.state.jobs._generator._llm = llm
    return llm


def test_an_endless_show_is_ready_after_its_first_song(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    created = _create(client_with_jamendo, source="jamendo", endless=True, track_count=5)
    show = client_with_jamendo.get(f"/api/v1/shows/{created['id']}").json()

    assert created["config"]["endless"] is True
    assert created["config"]["track_count"] == 0  # the count asked for is ignored
    assert show["status"] == "ready", show["error"]
    assert _kinds(show) == ["intro", "song"]
    assert show["config"]["track_count"] == 1  # the songs so far
    assert show["playback"] == "segments"
    assert show["audio_url"] is None
    assert show["end_note"] is None
    assert show["progress"] is None
    assert client_with_jamendo.get(f"/api/v1/shows/{created['id']}/audio").status_code == 404
    assert not (fake_settings.output_dir / created["id"] / "show.mp3").exists()
    # Every segment has its own file, the song too: a Jamendo song is not a video.
    for index, segment in enumerate(show["segments"]):
        assert segment["audio_url"] == f"/api/v1/shows/{created['id']}/segments/{index}/audio"
    song = client_with_jamendo.get(show["segments"][1]["audio_url"])
    assert song.status_code == 200
    assert song.headers["content-type"].startswith("audio/mpeg")
    assert song.content.startswith(b"ID3") or song.content.startswith(b"\xff")
    # Past shows list it as a ready show.
    (summary,) = client_with_jamendo.get("/api/v1/shows").json()
    assert summary["status"] == "ready" and summary["config"]["endless"] is True


def test_a_jamendo_show_of_a_set_length_still_keeps_its_songs_in_the_mp3(
    client_with_jamendo: TestClient,
) -> None:
    created = _create(client_with_jamendo, source="jamendo")
    show = client_with_jamendo.get(f"/api/v1/shows/{created['id']}").json()

    assert show["status"] == "ready", show["error"]
    assert show["playback"] == "file"
    assert show["config"]["endless"] is False
    assert show["end_note"] is None
    assert show["segments"][1]["kind"] == "song"
    assert show["segments"][1]["audio_file"] is not None
    assert show["segments"][1]["audio_url"] is None
    base = f"/api/v1/shows/{created['id']}/segments"
    assert client_with_jamendo.get(f"{base}/1/audio").status_code == 404
    assert client_with_jamendo.get(f"{base}/0/audio").status_code == 200


def test_an_endless_show_grows_part_by_part_and_ends_on_request(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    client = client_with_jamendo
    llm = _count_llm_calls(client)
    show_id = _create(client, source="jamendo", endless=True)["id"]
    show_dir = fake_settings.output_dir / show_id
    assert len(llm.calls) == 1  # the intro

    # The next part: a break and a song.
    response = _next(client, show_id, known=2)
    assert response.status_code == 200, response.text
    grown = response.json()
    assert _kinds(grown) == ["intro", "song", "transition", "song"]
    assert grown["status"] == "ready" and grown["end_note"] is None
    assert grown["config"]["track_count"] == 2
    assert len(grown["tracks"]) == 2
    assert grown["segments"][3]["audio_url"] == f"/api/v1/shows/{show_id}/segments/3/audio"
    assert client.get(grown["segments"][3]["audio_url"]).status_code == 200
    assert len(llm.calls) == 2

    # The same request again, from a retry or another tab: the part is there, nothing is made.
    again = _next(client, show_id, known=2)
    assert again.status_code == 200
    assert again.json() == grown
    assert len(llm.calls) == 2

    longer = _next(client, show_id, known=4).json()
    assert _kinds(longer) == ["intro", "song", "transition", "song", "transition", "song"]
    assert len(llm.calls) == 3
    dropped_track = longer["tracks"][2]["id"]
    assert (show_dir / "04-transition.mp3").exists() and (show_dir / "05-song.mp3").exists()

    # "End after this song" during the second song (segment 3): the part made ahead is
    # dropped and the goodbye takes its place.
    response = _end(client, show_id, after=3)
    assert response.status_code == 200, response.text
    ended = response.json()
    assert _kinds(ended) == ["intro", "song", "transition", "song", "outro"]
    assert ended["segments"][:4] == longer["segments"][:4]
    assert [track["id"] for track in ended["tracks"]] == [t["id"] for t in longer["tracks"][:2]]
    assert dropped_track not in [track["id"] for track in ended["tracks"]]
    assert ended["config"]["track_count"] == 2
    assert ended["end_note"] is None  # the listener ended it
    assert ended["status"] == "ready"
    assert ended["segments"][4]["text"] == "Script."
    assert client.get(ended["segments"][4]["audio_url"]).status_code == 200
    assert sorted(path.name for path in show_dir.iterdir() if path.suffix == ".mp3") == [
        "00-intro.mp3",
        "01-song.mp3",
        "02-transition.mp3",
        "03-song.mp3",
        "04-outro.mp3",
    ]
    assert len(llm.calls) == 4

    # A closed show is returned as it is, whatever is asked. A late request may still
    # know the six segments the show had before the end dropped a part.
    assert _end(client, show_id, after=1).json() == ended
    assert _next(client, show_id, known=6).json() == ended
    assert _next(client, show_id, known=5).json() == ended
    assert _next(client, show_id, known=2).json() == ended
    assert client.get(f"/api/v1/shows/{show_id}").json() == ended
    assert len(llm.calls) == 4


def test_a_dropped_file_that_cannot_be_deleted_does_not_fail_the_end(
    client_with_jamendo: TestClient, fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses to delete a file a player is still reading. The show is saved by
    then, so the request succeeds and the file stays."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    assert _next(client, show_id, known=2).status_code == 200

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError(f"{self.name} is in use")

    monkeypatch.setattr(Path, "unlink", refuse)
    response = _end(client, show_id, after=1)
    monkeypatch.undo()

    assert response.status_code == 200, response.text
    assert _kinds(response.json()) == ["intro", "song", "outro"]
    assert client.get(f"/api/v1/shows/{show_id}").json() == response.json()
    show_dir = fake_settings.output_dir / show_id
    assert (show_dir / "02-transition.mp3").exists() and (show_dir / "03-song.mp3").exists()


def test_end_never_drops_more_than_the_part_made_ahead(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    """`end` is the only request that deletes a part of a show, so it is bounded (D044):
    one break and one song at most, which is all a player can be ahead."""
    client = client_with_jamendo
    llm = _count_llm_calls(client)
    show_id = _create(client, source="jamendo", endless=True)["id"]
    show_dir = fake_settings.output_dir / show_id
    for known in (2, 4):
        assert _next(client, show_id, known).status_code == 200
    saved = (show_dir / "show.json").read_bytes()
    files = sorted(path.name for path in show_dir.iterdir() if path.suffix == ".mp3")
    calls = len(llm.calls)

    # After the first song there are two songs: refused, and nothing is touched.
    response = _end(client, show_id, after=1)

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            f"Show {show_id} has 2 songs after segment 1: only the part made ahead can be dropped."
        )
    }
    assert (show_dir / "show.json").read_bytes() == saved
    assert sorted(path.name for path in show_dir.iterdir() if path.suffix == ".mp3") == files
    assert len(llm.calls) == calls

    # After the second song there is one: dropped, as the player asks.
    response = _end(client, show_id, after=3)

    assert response.status_code == 200, response.text
    assert _kinds(response.json()) == ["intro", "song", "transition", "song", "outro"]


def test_end_after_the_last_song_drops_nothing(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    grown = _next(client, show_id, known=2).json()

    response = _end(client, show_id, after=3)

    assert response.status_code == 200, response.text
    ended = response.json()
    assert _kinds(ended) == ["intro", "song", "transition", "song", "outro"]
    assert ended["segments"][:4] == grown["segments"]
    assert ended["tracks"] == grown["tracks"]
    assert ended["config"]["track_count"] == 2
    assert ended["end_note"] is None
    show_dir = fake_settings.output_dir / show_id
    assert sorted(path.name for path in show_dir.iterdir() if path.suffix == ".mp3") == [
        "00-intro.mp3",
        "01-song.mp3",
        "02-transition.mp3",
        "03-song.mp3",
        "04-outro.mp3",
    ]


def test_an_endless_show_closes_by_itself_when_the_source_is_empty(
    client_with_jamendo: TestClient,
) -> None:
    """The Jamendo fixture has four jazz tracks."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    for known in (2, 4, 6):
        assert _next(client, show_id, known).status_code == 200

    show = _next(client, show_id, known=8).json()

    assert _kinds(show)[-1] == "outro" and len(show["tracks"]) == 4
    assert show["end_note"] == END_NOTE_NO_SONGS
    assert show["end_note"].startswith("The show ended by itself")
    assert show["status"] == "ready"


def test_an_endless_youtube_show_keeps_its_songs_in_the_embedded_player(
    client_with_youtube: TestClient,
) -> None:
    client = client_with_youtube
    created = _create(client, style=None, source="youtube", wish="trains", endless=True)

    show = _next(client, created["id"], known=2).json()

    assert _kinds(show) == ["intro", "song", "transition", "song"]
    assert show["playback"] == "segments"
    for index, segment in enumerate(show["segments"]):
        if segment["kind"] == "song":
            assert segment["audio_url"] is None and segment["audio_file"] is None
            assert segment["duration_s"] > 0
        else:
            assert segment["audio_url"] == f"/api/v1/shows/{created['id']}/segments/{index}/audio"
    assert all(track["video_id"] for track in show["tracks"])
    assert client.get(f"/api/v1/shows/{created['id']}/segments/1/audio").status_code == 404


def test_an_endless_show_on_the_catalog_is_a_400(client: TestClient) -> None:
    response = client.post("/api/v1/shows", json={"style": "jazz", "endless": True})

    assert response.status_code == 400
    assert response.json() == {
        "detail": "An endless show needs the 'jamendo' or 'youtube' music source."
    }
    assert client.get("/api/v1/shows").json() == []


def test_steps_on_an_unknown_show_are_a_404(client: TestClient) -> None:
    for show_id in ("20261030-120000-abcdef", "not-an-id"):
        assert _next(client, show_id, known=2).status_code == 404
        assert _end(client, show_id, after=1).status_code == 404


def test_steps_a_show_cannot_take_are_a_409(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    client = client_with_jamendo
    llm = _count_llm_calls(client)
    fixed = _create(client, source="jamendo")["id"]
    endless = _create(client, source="jamendo", endless=True)["id"]
    saved = (fake_settings.output_dir / endless / "show.json").read_bytes()
    calls = len(llm.calls)

    # Not an endless show.
    for response in (_next(client, fixed, known=7), _end(client, fixed, after=5)):
        assert response.status_code == 409
        assert response.json() == {"detail": f"Show {fixed} is not an endless show."}
    # More segments known than the show has.
    response = _next(client, endless, known=3)
    assert response.status_code == 409
    assert response.json() == {"detail": f"Show {endless} has 2 segments, not 3."}
    # Ending after something that is not a song: the intro, then past the end.
    for after in (0, 2):
        response = _end(client, endless, after=after)
        assert response.status_code == 409
        assert response.json() == {"detail": f"Segment {after} of show {endless} is not a song."}
    # Not ready yet: the opening is still being made.
    opening = Show(
        id="20261030-120000-abcdef",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", source="jamendo", endless=True),
        status="generating",
    )
    save_show(opening, fake_settings.output_dir / opening.id)
    for response in (_next(client, opening.id, known=0), _end(client, opening.id, after=1)):
        assert response.status_code == 409
        assert response.json() == {"detail": f"Show {opening.id} is generating, not ready yet."}

    # Nothing was generated and nothing changed.
    assert len(llm.calls) == calls
    assert (fake_settings.output_dir / endless / "show.json").read_bytes() == saved


def test_a_failed_endless_show_takes_no_step(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    failed = Show(
        id="20261030-120000-abcdef",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", source="jamendo", endless=True),
        status="failed",
        error="Style 'jazz' has 0 tracks, 1 needed.",
    )
    save_show(failed, fake_settings.output_dir / failed.id)

    for response in (
        _next(client_with_jamendo, failed.id, known=0),
        _end(client_with_jamendo, failed.id, after=1),
    ):
        assert response.status_code == 409
        assert response.json() == {"detail": f"Show {failed.id} is failed, not ready."}


def test_a_step_needs_a_valid_body(client_with_jamendo: TestClient) -> None:
    show_id = _create(client_with_jamendo, source="jamendo", endless=True)["id"]

    for body in ({}, {"known": -1}, {"known": "two"}):
        response = client_with_jamendo.post(f"/api/v1/shows/{show_id}/next", json=body)
        assert response.status_code == 422
    for body in ({}, {"after": -1}):
        response = client_with_jamendo.post(f"/api/v1/shows/{show_id}/end", json=body)
        assert response.status_code == 422


def test_a_failed_step_is_a_502_and_keeps_the_show(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    show_dir = fake_settings.output_dir / show_id
    before = client.get(f"/api/v1/shows/{show_id}").json()
    saved = (show_dir / "show.json").read_bytes()
    client.app.state.jobs._generator._llm = FakeLLM([""])  # an empty script: an LLMError

    for response in (_next(client, show_id, known=2), _end(client, show_id, after=1)):
        assert response.status_code == 502
        assert "returned an empty" in response.json()["detail"]

    # show.json is written only when a step succeeds: the show is as it was, still open.
    assert (show_dir / "show.json").read_bytes() == saved
    assert client.get(f"/api/v1/shows/{show_id}").json() == before
    assert client.get("/api/v1/options").status_code == 200
    # The same request works once the provider answers again.
    _count_llm_calls(client)
    assert _kinds(_next(client, show_id, known=2).json()) == [
        "intro",
        "song",
        "transition",
        "song",
    ]


def test_a_failed_end_keeps_the_part_it_meant_to_drop(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    """The goodbye is written and the show saved before anything is deleted. When the
    goodbye fails, the listener's show still has the part made ahead."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    show_dir = fake_settings.output_dir / show_id
    before = _next(client, show_id, known=2).json()
    assert _kinds(before) == ["intro", "song", "transition", "song"]
    saved = (show_dir / "show.json").read_bytes()
    client.app.state.jobs._generator._llm = FakeLLM([""])  # an empty script: an LLMError

    response = _end(client, show_id, after=1)

    assert response.status_code == 502
    assert "returned an empty" in response.json()["detail"]
    assert (show_dir / "show.json").read_bytes() == saved
    assert client.get(f"/api/v1/shows/{show_id}").json() == before
    for index, name in ((2, "02-transition.mp3"), (3, "03-song.mp3")):
        assert (show_dir / name).is_file()
        assert client.get(before["segments"][index]["audio_url"]).status_code == 200
    assert not (show_dir / "02-outro.mp3").exists()


def test_a_step_jamendo_does_not_answer_is_a_502_and_the_show_stays_open(
    client_with_jamendo: TestClient, fake_jamendo: FakeJamendo, fake_settings: Settings
) -> None:
    """Jamendo now and then answers no results for a style that has thousands, in runs
    (D044). The step fails, the host does not sign off, and the player asks again."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    before = client.get(f"/api/v1/shows/{show_id}").json()
    saved = (fake_settings.output_dir / show_id / "show.json").read_bytes()
    fake_jamendo.empty_replies = 99

    response = _next(client, show_id, known=2)

    assert response.status_code == 502
    assert response.json() == {
        "detail": "Jamendo did not answer the search. Try again in a moment."
    }
    assert (fake_settings.output_dir / show_id / "show.json").read_bytes() == saved
    assert client.get(f"/api/v1/shows/{show_id}").json() == before
    assert _kinds(before) == ["intro", "song"] and before["end_note"] is None

    fake_jamendo.empty_replies = 0
    response = _next(client, show_id, known=2)

    assert response.status_code == 200, response.text
    assert _kinds(response.json()) == ["intro", "song", "transition", "song"]


def test_a_step_that_cannot_be_saved_is_a_502_and_keeps_the_show(
    client_with_jamendo: TestClient, fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """show.json is written last. When that fails, the file on disk is the show as it
    was, and the same request works once the disk does."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    show_dir = fake_settings.output_dir / show_id
    before = client.get(f"/api/v1/shows/{show_id}").json()
    saved = (show_dir / "show.json").read_bytes()

    def disk_full(show: Show, out_dir: Path) -> Path:
        raise OSError("No space left on device")

    monkeypatch.setattr(jobs_module, "save_show", disk_full)
    response = _next(client, show_id, known=2)
    monkeypatch.undo()

    assert response.status_code == 502
    assert response.json() == {"detail": "No space left on device"}
    assert (show_dir / "show.json").read_bytes() == saved
    assert client.get(f"/api/v1/shows/{show_id}").json() == before
    assert _kinds(_next(client, show_id, known=2).json()) == [
        "intro",
        "song",
        "transition",
        "song",
    ]


def test_an_endless_show_survives_a_restart(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_jamendo
) -> None:
    """An open show is `ready` on disk, so the startup check leaves it alone, and the
    next part is made from show.json by a generator that never saw the show (D044)."""
    sources = {"catalog": tiny_catalog, "jamendo": fake_jamendo.source}
    with TestClient(create_app(fake_settings, sources)) as client:
        show_id = _create(client, source="jamendo", endless=True)["id"]
        first = _next(client, show_id, known=2).json()

    with TestClient(create_app(fake_settings, sources)) as client:
        show = client.get(f"/api/v1/shows/{show_id}").json()
        assert show == first
        assert show["status"] == "ready" and show["error"] is None

        longer = _next(client, show_id, known=4).json()

    assert _kinds(longer) == ["intro", "song", "transition", "song", "transition", "song"]
    assert longer["segments"][:4] == first["segments"]
    assert len({track["id"] for track in longer["tracks"]}) == 3


class BlockingGenerator:
    """A generator whose `extend` waits until the test lets it go, and counts its calls."""

    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def extend(self, show: Show, out_dir: Path, *, end: bool = False) -> Show:
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=10)
        show.segments += [Segment(kind="transition", text="Next."), Segment(kind="song")]
        return show


class WatchedLock:
    """A lock that tells when a caller has to wait for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.contended = threading.Event()

    def __enter__(self) -> None:
        if not self._lock.acquire(blocking=False):
            self.contended.set()
            self._lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


def test_two_requests_for_the_same_part_make_it_once(tmp_path: Path) -> None:
    """The second request waits on the lock (D011), then finds the part the first one
    made. No sleep: each thread is released by an event."""
    show = Show(
        id="20261030-120000-abcdef",
        created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        config=ShowConfig(style="jazz", persona="calm", source="jamendo", endless=True),
        status="ready",
        segments=[Segment(kind="intro", text="Hello."), Segment(kind="song")],
    )
    save_show(show, tmp_path / show.id)
    generator = BlockingGenerator()
    jobs = Jobs(generator, tmp_path)  # type: ignore[arg-type]
    lock = WatchedLock()
    jobs._lock = lock  # type: ignore[assignment]
    results: dict[str, Show] = {}

    def ask(name: str) -> None:
        results[name] = jobs.extend(show.id, known=2)

    first = threading.Thread(target=ask, args=("first",))
    second = threading.Thread(target=ask, args=("second",))
    first.start()
    assert generator.entered.wait(timeout=10)  # the first request is making the part
    second.start()
    assert lock.contended.wait(timeout=10)  # the second one is waiting for the lock
    generator.release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert generator.calls == 1
    assert [len(results[name].segments) for name in ("first", "second")] == [4, 4]
    assert len(load_show(tmp_path / show.id).segments) == 4


def test_a_step_reads_show_json_under_the_lock_only(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    """A request that arrives while another one saves the show waits for the lock. It
    must not read the file before: at that instant the file is half-written."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    show_file = fake_settings.output_dir / show_id / "show.json"
    whole = show_file.read_bytes()
    app_jobs = client.app.state.jobs
    lock = WatchedLock()
    app_jobs._lock = lock
    replies: list[ShowResponse] = []

    def ask() -> None:
        replies.append(next_part(show_id, NextPartRequest(known=2), fake_settings, app_jobs))

    with lock:  # another request is in the middle of its save
        show_file.write_bytes(whole[: len(whole) // 2])
        request = threading.Thread(target=ask)
        request.start()
        assert lock.contended.wait(timeout=10)  # the new request is waiting, not reading
        show_file.write_bytes(whole)
    request.join(timeout=30)

    assert [len(reply.segments) for reply in replies] == [4]


def test_segment_audio_is_never_reused_without_asking_the_server(
    client_with_jamendo: TestClient, fake_settings: Settings
) -> None:
    """After an `end`, the URL of the dropped break is the URL of the goodbye (D044)."""
    client = client_with_jamendo
    show_id = _create(client, source="jamendo", endless=True)["id"]
    assert _next(client, show_id, known=2).status_code == 200
    url = f"/api/v1/shows/{show_id}/segments/2/audio"
    dropped_break = client.get(url)
    assert dropped_break.headers["cache-control"] == "no-cache"

    assert _end(client, show_id, after=1).status_code == 200
    goodbye = client.get(url)

    assert goodbye.status_code == 200
    assert goodbye.headers["cache-control"] == "no-cache"
    show_dir = fake_settings.output_dir / show_id
    assert goodbye.content == (show_dir / "02-outro.mp3").read_bytes()
    # Songs are served by the same route, with the same header.
    assert client.get(f"/api/v1/shows/{show_id}/segments/1/audio").headers["cache-control"] == (
        "no-cache"
    )


def test_the_schema_describes_the_endless_show(fake_settings: Settings) -> None:
    schema = create_app(fake_settings).openapi()

    assert "post" in schema["paths"]["/api/v1/shows/{show_id}/next"]
    assert "post" in schema["paths"]["/api/v1/shows/{show_id}/end"]
    schemas = schema["components"]["schemas"]
    assert schemas["NextPartRequest"]["required"] == ["known"]
    assert schemas["EndShowRequest"]["required"] == ["after"]
    assert schemas["CreateShowRequest"]["properties"]["endless"]["default"] is False
    assert "endless" in schemas["ShowConfig"]["properties"]
    assert "endless" in schemas["SourceOption"]["required"]
    assert "end_note" in schemas["ShowResponse"]["properties"]
