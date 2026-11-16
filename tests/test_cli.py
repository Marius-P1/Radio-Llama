import json
import logging
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from radio_llama import cli
from radio_llama.cli import app
from radio_llama.config import Settings
from radio_llama.generator import PipelineGenerator
from radio_llama.llm import FakeLLM
from radio_llama.music import LocalCatalog
from radio_llama.voice import CachedTextToSpeech, FakeTTS
from tests.conftest import FakeYouTube, refused_voices

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


def test_generate_with_a_voice_saves_its_name_and_passes_it_to_the_tts(cli_env: Path) -> None:
    result = runner.invoke(
        app, ["generate", "--fake", "--style", "jazz", "--voice", "fake-voice-2"]
    )

    assert result.exit_code == 0, result.output
    show_dir = next(cli_env.iterdir())
    show = json.loads((show_dir / "show.json").read_text(encoding="utf-8"))
    assert show["config"]["voice_id"] == "fake-voice-2"
    assert show["config"]["voice_name"] == "Basile"  # looked up in the voice list (D040)
    lines = (show_dir / "trace" / "tts.jsonl").read_text(encoding="utf-8").splitlines()
    assert lines
    assert all(json.loads(line)["voice_id"] == "fake-voice-2" for line in lines)


def test_generate_with_an_unknown_voice_stops_before_generating(cli_env: Path) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", "--voice", "nobody"])

    assert result.exit_code == 1
    assert "Unknown voice 'nobody'" in result.output
    assert "Generating show" not in result.output
    assert "Traceback" not in result.output
    assert not cli_env.exists()


def test_generate_reports_a_voice_list_that_cannot_be_read(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(cli, "make_voice_list", lambda settings: refused_voices())

    with caplog.at_level(logging.WARNING, logger="radio_llama.voice.elevenlabs"):
        result = runner.invoke(
            app, ["generate", "--fake", "--style", "jazz", "--voice", "fake-voice-2"]
        )

    # The error the command prints is the same short sentence as the API's. What
    # ElevenLabs answered is a warning in the log, which a terminal shows on stderr.
    assert result.exit_code == 1
    assert "Could not list the ElevenLabs voices (HTTP 401)." in result.output
    assert "Invalid API key" in caplog.text
    assert "Traceback" not in result.output
    assert not cli_env.exists()
    # Without --voice the list is not read, so the show is made.
    assert runner.invoke(app, ["generate", "--fake", "--style", "jazz"]).exit_code == 0


@pytest.mark.parametrize("args", [[], ["--voice", ""], ["--voice", "   "]])
def test_generate_without_a_voice_stores_none(cli_env: Path, args: list[str]) -> None:
    result = runner.invoke(app, ["generate", "--fake", "--style", "jazz", *args])

    assert result.exit_code == 0, result.output
    show = json.loads((next(cli_env.iterdir()) / "show.json").read_text(encoding="utf-8"))
    assert show["config"]["voice_id"] is None  # a blank voice is no voice
    assert show["config"]["voice_name"] is None


def test_script_has_no_voice_option(cli_env: Path) -> None:
    result = runner.invoke(app, ["script", "--fake", "--style", "jazz", "--voice", "my-voice"])

    assert result.exit_code == 2
    assert "No such option" in result.output


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


def test_youtube_without_a_key_explains_the_variable(cli_env: Path) -> None:
    for command in ("generate", "script"):
        result = runner.invoke(
            app, [command, "--fake", "--source", "youtube", "--wish", "songs about trains"]
        )

        assert result.exit_code == 1
        assert "YOUTUBE_API_KEY" in result.output
        assert "Traceback" not in result.output


# No style on YouTube (D039): --wish is the one required option there.


def test_youtube_refuses_a_style(cli_env: Path) -> None:
    for command in ("generate", "script"):
        result = runner.invoke(
            app, [command, "--fake", "--source", "youtube", "--style", "jazz", "--wish", "trains"]
        )

        assert result.exit_code == 1
        assert "--style is not used with --source youtube" in result.output
        assert "--wish" in result.output
        assert "Traceback" not in result.output
    assert not cli_env.exists()


def test_youtube_needs_a_wish(cli_env: Path) -> None:
    for args in (["--source", "youtube"], ["--source", "youtube", "--wish", "  "]):
        result = runner.invoke(app, ["generate", "--fake", *args])

        assert result.exit_code == 1
        assert "--source youtube needs --wish" in result.output
    assert not cli_env.exists()


def test_other_sources_need_a_style(cli_env: Path) -> None:
    for command, source in (("generate", "catalog"), ("script", "jamendo")):
        result = runner.invoke(app, [command, "--fake", "--source", source])

        assert result.exit_code == 1
        assert f"--style is required with --source {source}" in result.output
    assert not cli_env.exists()


def test_generate_youtube_prints_the_folder(
    cli_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An embed show has no show.mp3, so the command names the folder instead (D038)."""
    playlist = (
        '{"songs": [{"artist": "Example Artist", "title": "Blue Window"}, '
        '{"artist": "Lea Example", "title": "Montreal Streets"}, '
        '{"artist": "We Are FM", "title": "Night Train"}]}'
    )
    youtube = FakeYouTube(tmp_path / "cache")

    def fake_generator(settings):
        tts = CachedTextToSpeech(FakeTTS(settings.cache_dir / "tts-raw"), settings.cache_dir)
        llm = FakeLLM([playlist, "This is a fake radio script."])
        return PipelineGenerator(llm, tts, {"youtube": youtube.source}, settings)

    monkeypatch.setattr(cli, "make_generator", fake_generator)

    result = runner.invoke(
        app, ["generate", "--fake", "--source", "youtube", "--wish", "90s French rap, upbeat"]
    )

    assert result.exit_code == 0, result.output
    (show_dir,) = cli_env.iterdir()
    assert f"Show written to {show_dir}" in result.output
    assert "no show.mp3" in result.output
    assert not (show_dir / "show.mp3").exists()
    assert (show_dir / "00-intro.mp3").exists()
    show = json.loads((show_dir / "show.json").read_text(encoding="utf-8"))
    assert show["audio_file"] is None
    assert show["tracks"][0]["video_id"]
    assert show["config"]["style"] is None  # no style on YouTube (D039)
    assert show["config"]["wish"] == "90s French rap, upbeat"
