import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from radio_llama.cli import app
from radio_llama.config import Settings
from radio_llama.music import LocalCatalog

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

runner = CliRunner()


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tiny_catalog: LocalCatalog) -> Path:
    """Point the settings at the tiny catalog and tmp folders; no keys, no .env. Returns output."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    output = tmp_path / "output"
    monkeypatch.setenv("CATALOG_PATH", str(tiny_catalog.catalog_path))
    monkeypatch.setenv("OUTPUT_DIR", str(output))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PROMPTS_DIR", str(Path(__file__).resolve().parent.parent / "prompts"))
    return output


def test_generate_fake_writes_an_mp3(cli_env: Path) -> None:
    result = runner.invoke(
        app, ["generate", "--fake", "--style", "jazz", "--seed", "1", "--song-length", "1.5"]
    )

    assert result.exit_code == 0, result.output
    assert "show.mp3" in result.output
    assert "LLM tokens:" in result.output
    shows = list(cli_env.iterdir())
    assert len(shows) == 1
    assert (shows[0] / "show.mp3").exists()
    assert (shows[0] / "show.json").exists()


def test_generate_with_energy_records_it(cli_env: Path) -> None:
    result = runner.invoke(
        app,
        ["generate", "--fake", "--style", "rock", "--energy", "calm", "--song-length", "1.5"],
    )

    assert result.exit_code == 0, result.output
    show = json.loads((next(cli_env.iterdir()) / "show.json").read_text(encoding="utf-8"))
    assert show["config"]["energy"] == "calm"


def test_generate_with_unknown_energy_fails_cleanly(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--energy", "frantic"])

    assert result.exit_code == 1
    assert "energy" in result.output
    assert "Traceback" not in result.output


def test_generate_without_keys_explains_the_missing_variable(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--style", "jazz"])

    assert result.exit_code == 1
    assert "MISTRAL_API_KEY" in result.output
    assert "Traceback" not in result.output


def test_generate_with_too_many_tracks_fails_cleanly(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--tracks", "99"])

    assert result.exit_code == 1
    assert "needed" in result.output
    assert "Traceback" not in result.output
