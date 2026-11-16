"""Tests for the `/api/v1` routes with the fake LLM and fake TTS.

FastAPI's TestClient runs background tasks before the response reaches the test, so a
POST /shows here completes the whole generation before `client.post` returns. The
`pending` status is still visible in the POST body because it is built before the task.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from radio_llama.api.app import create_app
from radio_llama.cli import app as cli_app
from radio_llama.config import Settings
from radio_llama.llm import FakeLLM
from radio_llama.models import Show, ShowConfig
from radio_llama.music import LocalCatalog
from radio_llama.storage import save_show
from radio_llama.voice import TTSError

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
        {"id": "catalog", "name": "Local catalog", "styles": ["jazz", "rock"], "wish": False}
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
    for track in show["tracks"]:
        assert track["license"] and track["license_url"]
        assert track["track_url"] and track["artist_url"]
        assert "notes" not in track

    audio = client.get(show["audio_url"])
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/mpeg")
    assert audio.content.startswith(b"ID3") or audio.content.startswith(b"\xff")


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
    assert "/api/v1/shows/{show_id}/audio" in schema["paths"]


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
