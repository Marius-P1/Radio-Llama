import shutil
import subprocess
from pathlib import Path

import pytest

from radio_llama.voice import FakeTTS, TTSError, Voice

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are needed to make and measure silence",
)

VOICE = Voice(voice_id="any", model_id="any")


def duration_s(path: Path) -> float:
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration"]
    command += ["-of", "default=nw=1:nk=1", str(path)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def test_duration_follows_text_length(tmp_path: Path) -> None:
    text = "a" * 100
    fake = FakeTTS(tmp_path)
    path = fake.synthesize(text, VOICE)
    expected = len(text) * 0.06
    assert path.exists()
    assert path.parent == tmp_path
    assert abs(duration_s(path) - expected) <= 0.15 * expected
    assert fake.calls == 1


def test_minimum_half_second_for_empty_text(tmp_path: Path) -> None:
    path = FakeTTS(tmp_path).synthesize("", VOICE)
    assert abs(duration_s(path) - 0.5) <= 0.15 * 0.5


def test_ffmpeg_failure_becomes_tts_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_run(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"line one\nsomething broke")

    monkeypatch.setattr(subprocess, "run", failing_run)
    with pytest.raises(TTSError, match="something broke"):
        FakeTTS(tmp_path).synthesize("hello", VOICE)
