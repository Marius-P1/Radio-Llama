"""Thin wrappers around the ffmpeg and ffprobe command line tools.

Every call is a plain `subprocess.run` with a list of arguments and no shell.
"""

import subprocess
from pathlib import Path

STDERR_TAIL_LINES = 5  # how many lines of ffmpeg's stderr go into an AudioError


class AudioError(Exception):
    """An ffmpeg or ffprobe call failed. The message includes the last lines of stderr."""


def _tail(stderr: str) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return "\n".join(lines[-STDERR_TAIL_LINES:])


def run_ffmpeg(args: list[str]) -> None:
    """Run `ffmpeg -v error -y <args>`. Raise AudioError on a non-zero exit."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AudioError(f"ffmpeg failed: {_tail(result.stderr)}")


def probe_duration(path: Path) -> float:
    """Return the duration of an audio file in seconds, as reported by ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AudioError(f"ffprobe failed: {_tail(result.stderr)}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise AudioError(f"ffprobe gave no duration for {path}: {result.stdout!r}") from exc
