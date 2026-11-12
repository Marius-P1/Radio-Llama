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
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--seed", "1"])

    assert result.exit_code == 0, result.output
    assert "show.mp3" in result.output
    assert "LLM tokens:" in result.output
    shows = list(cli_env.iterdir())
    assert len(shows) == 1
    assert (shows[0] / "show.mp3").exists()
    show = json.loads((shows[0] / "show.json").read_text(encoding="utf-8"))
    assert show["config"]["demo"] is False  # full songs by default (D023)


def test_generate_demo_records_it(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--demo"])

    assert result.exit_code == 0, result.output
    show = json.loads((next(cli_env.iterdir()) / "show.json").read_text(encoding="utf-8"))
    assert show["config"]["demo"] is True


def test_generate_with_energy_records_it(cli_env: Path) -> None:
    result = runner.invoke(
        app,
        ["generate", "--fake", "--style", "rock", "--energy", "calm"],
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


def test_script_prints_the_segments_and_writes_only_a_trace(cli_env: Path) -> None:
    result = runner.invoke(app, ["script", "--fake", "--style", "jazz", "--seed", "1"])

    assert result.exit_code == 0, result.output
    assert result.output.count("[song]") == 3
    assert "[intro, " in result.output
    assert "[outro, " in result.output
    assert result.output.count("[transition: ") == 2
    assert "This is a fake radio script." in result.output
    assert "LLM tokens:" in result.output
    # No show folder, no audio: only the trace under output/scripts/<show-id>/ (D037).
    assert [path.name for path in cli_env.iterdir()] == ["scripts"]
    (trace_dir,) = (cli_env / "scripts").iterdir()
    assert [path.name for path in trace_dir.iterdir()] == ["llm.jsonl"]
    assert f"Trace in {trace_dir}" in result.output


def test_script_that_fails_before_any_call_names_no_trace(cli_env: Path) -> None:
    result = runner.invoke(app, ["script", "--fake", "--style", "jazz", "--tracks", "99"])

    assert result.exit_code == 1
    assert "needed" in result.output
    assert "Trace in" not in result.output
    assert not cli_env.exists()


def test_script_jamendo_without_a_client_id_explains_the_variable(cli_env: Path) -> None:
    result = runner.invoke(app, ["script", "--fake", "--style", "jazz", "--source", "jamendo"])

    assert result.exit_code == 1
    assert "JAMENDO_CLIENT_ID" in result.output
    assert "Traceback" not in result.output


def test_generate_jamendo_without_a_client_id_explains_the_variable(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--source", "jamendo"])

    assert result.exit_code == 1
    assert "JAMENDO_CLIENT_ID" in result.output
    assert "Traceback" not in result.output


def test_generate_with_unknown_source_fails_cleanly(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--source", "spotify"])

    assert result.exit_code == 1
    assert "source" in result.output
    assert "Traceback" not in result.output
