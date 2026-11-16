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
from tests.conftest import FakeYouTube

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

runner = CliRunner()

READY_ID = "20261030-120000-abc123"
FAILED_ID = "20261030-130000-def456"
YOUTUBE_ID = "20261030-140000-abc789"
CONFIG = ShowConfig(style="jazz", persona="calm", seed=1)
YOUTUBE_CONFIG = ShowConfig(style=None, persona="calm", seed=1, source="youtube", wish="trains")
# The fake YouTube answers these three songs with playable videos (tests/conftest.py).
PLAYLIST_JSON = (
    '{"songs": [{"artist": "Example Artist", "title": "Blue Window"}, '
    '{"artist": "Lea Example", "title": "Montreal Streets"}, '
    '{"artist": "We Are FM", "title": "Night Train"}]}'
)
SPEECH_FILES = ["00-intro.mp3", "02-transition.mp3", "04-transition.mp3", "06-outro.mp3"]


@pytest.fixture
def output_dir(
    fake_settings: Settings, tiny_catalog: LocalCatalog, fake_youtube: FakeYouTube
) -> Path:
    """A ready catalog show and a ready YouTube show made with the fakes, plus a failed show."""
    tts = CachedTextToSpeech(FakeTTS(fake_settings.cache_dir / "tts-raw"), fake_settings.cache_dir)
    generator = PipelineGenerator(
        FakeLLM(["A fake script."]), tts, {"catalog": tiny_catalog}, fake_settings
    )
    ready = Show(id=READY_ID, created_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC), config=CONFIG)
    show = run_generation(generator, ready, fake_settings.output_dir / READY_ID)
    assert show.status == "ready"

    # An embed show: speech files next to show.json, no show.mp3 (D038).
    youtube_generator = PipelineGenerator(
        FakeLLM([PLAYLIST_JSON, "A fake script."]),
        tts,
        {"catalog": tiny_catalog, "youtube": fake_youtube.source},
        fake_settings,
    )
    youtube = Show(
        id=YOUTUBE_ID, created_at=datetime(2026, 10, 30, 14, 0, tzinfo=UTC), config=YOUTUBE_CONFIG
    )
    show = run_generation(youtube_generator, youtube, fake_settings.output_dir / YOUTUBE_ID)
    assert show.status == "ready", show.error
    assert show.audio_file is None

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


def test_export_leaves_the_trace_behind(output_dir: Path, tmp_path: Path) -> None:
    """The prompts in the trace are not for the public page (D037)."""
    assert (output_dir / READY_ID / "trace" / "llm.jsonl").exists()
    dest = tmp_path / "shows"

    export_replay([READY_ID], output_dir, dest)
    zip_path = zip_replay(dest, tmp_path / "replay-shows.zip")

    assert sorted(path.name for path in (dest / READY_ID).iterdir()) == ["show.json", "show.mp3"]
    with zipfile.ZipFile(zip_path) as archive:
        assert not [name for name in archive.namelist() if "trace" in name]


def test_export_refuses_a_show_that_is_not_ready(output_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "shows"

    with pytest.raises(ValueError, match=f"{FAILED_ID} is failed"):
        export_replay([FAILED_ID], output_dir, dest)

    assert not (dest / FAILED_ID).exists()


def test_export_copies_the_speech_files_of_a_youtube_show(output_dir: Path, tmp_path: Path) -> None:
    """An embed show has no show.mp3: its host segments travel as one file each (D043)."""
    dest = tmp_path / "shows"

    summaries = export_replay([YOUTUBE_ID], output_dir, dest)

    assert sorted(path.name for path in (dest / YOUTUBE_ID).iterdir()) == [
        *SPEECH_FILES,
        "show.json",
    ]
    for name in SPEECH_FILES:
        assert (dest / YOUTUBE_ID / name).read_bytes() == (
            output_dir / YOUTUBE_ID / name
        ).read_bytes()
    assert summaries[0]["id"] == YOUTUBE_ID
    index = json.loads((dest / "index.json").read_text(encoding="utf-8"))
    assert [entry["id"] for entry in index] == [YOUTUBE_ID]
    assert index[0]["config"]["source"] == "youtube"


def test_export_refuses_a_youtube_show_with_a_missing_speech_file(
    output_dir: Path, tmp_path: Path
) -> None:
    (output_dir / YOUTUBE_ID / "02-transition.mp3").unlink()
    dest = tmp_path / "shows"

    with pytest.raises(
        FileNotFoundError, match=f"{YOUTUBE_ID} has no audio file 02-transition.mp3"
    ):
        export_replay([YOUTUBE_ID], output_dir, dest)

    assert not (dest / YOUTUBE_ID).exists()


def test_export_refuses_a_file_show_without_its_mp3(output_dir: Path, tmp_path: Path) -> None:
    """A ready catalog show is one MP3: without it there is nothing to play."""
    broken_id = "20261030-150000-abc000"
    show = load_show(output_dir / READY_ID).model_copy(update={"id": broken_id, "audio_file": None})
    save_show(show, output_dir / broken_id)
    dest = tmp_path / "shows"

    with pytest.raises(ValueError, match=f"{broken_id} is ready but has no show.mp3"):
        export_replay([broken_id], output_dir, dest)

    assert not (dest / broken_id).exists()


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


def test_zip_holds_the_speech_files_of_a_youtube_show(output_dir: Path, tmp_path: Path) -> None:
    dest = tmp_path / "shows"
    export_replay([READY_ID, YOUTUBE_ID], output_dir, dest)

    zip_path = zip_replay(dest, tmp_path / "replay-shows.zip")

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert f"{YOUTUBE_ID}/show.json" in names
    assert {f"{YOUTUBE_ID}/{name}" for name in SPEECH_FILES} <= names
    assert f"{YOUTUBE_ID}/show.mp3" not in names
    assert f"{READY_ID}/show.mp3" in names


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


def test_cli_exports_a_youtube_show(cli_env: Path) -> None:
    result = runner.invoke(app, ["export-replay", YOUTUBE_ID, "--dest", str(cli_env)])

    assert result.exit_code == 0, result.output
    assert YOUTUBE_ID in result.output
    assert (cli_env / YOUTUBE_ID / "show.json").exists()
    for name in SPEECH_FILES:
        assert (cli_env / YOUTUBE_ID / name).exists()
    assert not (cli_env / YOUTUBE_ID / "show.mp3").exists()


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
