"""A fake backend that writes silence, so the whole pipeline runs without a paid API."""

import subprocess
from pathlib import Path
from uuid import uuid4

from radio_llama.voice.base import TTSError, Voice

MIN_SECONDS = 0.5


class FakeTTS:
    def __init__(self, work_dir: Path, seconds_per_char: float = 0.06) -> None:
        self._work_dir = work_dir
        self._seconds_per_char = seconds_per_char
        self.calls = 0

    def synthesize(self, text: str, voice: Voice) -> Path:
        self.calls += 1
        self._work_dir.mkdir(parents=True, exist_ok=True)
        seconds = max(MIN_SECONDS, len(text) * self._seconds_per_char)
        path = self._work_dir / f"{uuid4().hex}.mp3"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            str(seconds),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "9",
            "-y",
            str(path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            tail = exc.stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise TTSError(f"Fake TTS: ffmpeg failed (exit {exc.returncode}): {tail}") from exc
        except OSError as exc:
            raise TTSError(f"Fake TTS: could not run ffmpeg: {exc}") from exc
        return path
