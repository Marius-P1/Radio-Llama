"""Generate sine-tone MP3s for the placeholder tracks in catalog.json.

Run as `uv run python catalog/make_placeholder_audio.py`. Each placeholder track gets
a 25 second tone at its own pitch, written to `catalog/audio/<audio_file>` with ffmpeg.
Nothing is downloaded.
"""

import argparse
import json
import subprocess
from pathlib import Path

CATALOG_DIR = Path(__file__).resolve().parent
CATALOG_PATH = CATALOG_DIR / "catalog.json"
AUDIO_DIR = CATALOG_DIR / "audio"
TONE_SECONDS = 25.0


def make_tone(path: Path, frequency: float, seconds: float) -> None:
    """Write a sine tone of `seconds` at `frequency` Hz to `path` as MP3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration={seconds}",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "6",
            "-y",
            str(path),
        ],
        check=True,
    )


def generate(force: bool = False) -> list[Path]:
    """Create one tone per placeholder track. Returns the files written."""
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    placeholders = [track for track in catalog["tracks"] if track.get("placeholder")]
    written: list[Path] = []
    for index, track in enumerate(placeholders):
        out = AUDIO_DIR / track["audio_file"]
        if out.exists() and not force:
            continue
        frequency = 220 * 2 ** (index / 12)
        make_tone(out, frequency, TONE_SECONDS)
        print(f"wrote {out.relative_to(CATALOG_DIR.parent)} ({frequency:.1f} Hz)")
        written.append(out)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="overwrite files that already exist")
    args = parser.parse_args()
    generate(force=args.force)


if __name__ == "__main__":
    main()
