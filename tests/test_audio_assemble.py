import shutil
from pathlib import Path

import pytest

from radio_llama.audio.ffmpeg import AudioError, run_ffmpeg

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_run_ffmpeg_reports_failure(tmp_path: Path) -> None:
    with pytest.raises(AudioError, match="ffmpeg failed"):
        run_ffmpeg(["-i", str(tmp_path / "missing.mp3"), str(tmp_path / "out.mp3")])
