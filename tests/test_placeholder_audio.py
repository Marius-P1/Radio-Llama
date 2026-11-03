import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "fixtures" / "catalog" / "make_placeholder_audio.py"


def _load_make_tone():
    spec = importlib.util.spec_from_file_location("make_placeholder_audio", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_tone


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_make_tone_writes_an_mp3_of_the_requested_length(tmp_path: Path) -> None:
    make_tone = _load_make_tone()
    out = tmp_path / "tone.mp3"

    make_tone(out, 440.0, 2.0)

    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = float(result.stdout.strip())
    assert 1.8 <= duration <= 2.4
