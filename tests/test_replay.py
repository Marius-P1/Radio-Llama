import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from radio_llama.cli import app
from radio_llama.config import Settings
from radio_llama.generator import PipelineGenerator, run_generation
from radio_llama.llm import FakeLLM
from radio_llama.models import Show, ShowConfig
from radio_llama.music import LocalCatalog
from radio_llama.replay import export_replay, write_index, zip_replay
from radio_llama.storage import load_show, save_show
from radio_llama.voice import CachedTextToSpeech, FakeTTS

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

runner = CliRunner()

READY_ID = "20261030-120000-abc123"
FAILED_ID = "20261030-130000-def456"
CONFIG = ShowConfig(style="jazz", persona="calm", seed=1)


@pytest.fixture
def output_dir(fake_settings: Settings, tiny_catalog: LocalCatalog) -> Path:
    """One ready show made with the fakes and one failed show written by hand."""
    tts = CachedTextToSpeech(FakeTTS(fake_settings.cache_dir / "tts-raw"), fake_settings.cache_dir)
    generator = PipelineGenerator(
        FakeLLM(["A fake script."]), tts, {"catalog": tiny_catalog}, fake_settings
    )
    ready = Show(id=READY_ID, created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC), config=CONFIG)
    show = run_generation(generator, ready, fake_settings.output_dir / READY_ID)
    assert show.status == "ready"

    failed = Show(
        id=FAILED_ID,
        created_at=datetime(2026, 10, 30, 13, 0, tzinfo=UTC),
        config=CONFIG,
        status="failed",
        error="Fake TTS: the voice is out today",
    )
    save_show(failed, fake_settings.output_dir / FAILED_ID)
    return fake_settings.output_dir


def test_export_copies_the_ready_show(output_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "shows"

    summaries = export_replay([READY_ID], output_dir, dest)

    assert (dest / READY_ID / "show.json").read_bytes() == (
        output_dir / READY_ID / "show.json"
    ).read_bytes()
    assert (dest / READY_ID / "show.mp3").read_bytes() == (
        output_dir / READY_ID / "show.mp3"
    ).read_bytes()
    assert len(summaries) == 1
    assert summaries[0]["id"] == READY_ID
    assert summaries[0]["status"] == "ready"
    assert (dest / "index.json").exists()


def test_export_refuses_a_show_that_is_not_ready(output_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "shows"

    with pytest.raises(ValueError, match=f"{FAILED_ID} is failed"):
        export_replay([FAILED_ID], output_dir, dest)

    assert not (dest / FAILED_ID).exists()


def test_export_refuses_an_unknown_id(output_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="20200101-000000-000000"):
        export_replay(["20200101-000000-000000"], output_dir, tmp_path / "shows")


def test_index_lists_summaries_newest_first(output_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "shows"
    export_replay([READY_ID], output_dir, dest)
    # An older copy of the same show, so the order can be checked.
    older_id = "20261029-120000-000aaa"
    older = load_show(dest / READY_ID).model_copy(
        update={"id": older_id, "created_at": datetime(2026, 10, 29, 12, 0, tzinfo=UTC)}
    )
    save_show(older, dest / older_id)

    write_index(dest)

    text = (dest / "index.json").read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert "\r" not in text
    index = json.loads(text)
    assert [entry["id"] for entry in index] == [READY_ID, older_id]
    entry = index[0]
    assert set(entry) == {"id", "status", "created_at", "config", "error"}
    assert entry["status"] == "ready"
    assert entry["error"] is None
    assert entry["created_at"] == "2026-10-30T12:00:00Z"
    assert entry["config"]["style"] == "jazz"
    assert entry["config"]["persona"] == "calm"


def test_zip_holds_the_index_and_the_audio(output_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "shows"
    export_replay([READY_ID], output_dir, dest)

    zip_path = zip_replay(dest, tmp_path / "replay-shows.zip")

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "index.json" in names
    assert f"{READY_ID}/show.mp3" in names
    assert f"{READY_ID}/show.json" in names


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output_dir: Path) -> Path:
    """Point OUTPUT_DIR at the generated shows; no keys, no .env. Returns the destination."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("OUTPUT_DIR", str(output_dir))
    return tmp_path / "shows"


def test_cli_exports_and_zips(cli_env: Path, tmp_path: Path) -> None:
    zip_path = tmp_path / "replay-shows.zip"

    result = runner.invoke(
        app, ["export-replay", READY_ID, "--dest", str(cli_env), "--zip", str(zip_path)]
    )

    assert result.exit_code == 0, result.output
    assert READY_ID in result.output
    assert "index.json" in result.output
    assert "replay-shows.zip" in result.output
    assert (cli_env / READY_ID / "show.mp3").exists()
    assert (cli_env / "index.json").exists()
    assert zip_path.exists()


def test_cli_unknown_id_fails_cleanly(cli_env: Path) -> None:
    result = runner.invoke(app, ["export-replay", "20200101-000000-000000", "--dest", str(cli_env)])

    assert result.exit_code == 1
    assert "No show 20200101-000000-000000" in result.output
    assert "Traceback" not in result.output


def test_cli_failed_show_fails_cleanly(cli_env: Path) -> None:
    result = runner.invoke(app, ["export-replay", FAILED_ID, "--dest", str(cli_env)])

    assert result.exit_code == 1
    assert "not ready" in result.output
    assert "Traceback" not in result.output
