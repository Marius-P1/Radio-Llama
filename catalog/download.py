"""Fetch the catalog audio into `catalog/audio/`.

Run as `uv run python catalog/download.py`. With a placeholder-only catalog it generates
local test tones instead of downloading. With a real catalog it downloads the release
archive, checks its SHA-256 against `SHA256SUMS`, and unpacks it (D002).
"""

import hashlib
import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from make_placeholder_audio import generate as generate_placeholder_audio

ARCHIVE_URL = (
    "https://github.com/Marius-P1/Radio-Llama/releases/download/catalog-v1/catalog-audio.zip"
)
ARCHIVE_NAME = "catalog-audio.zip"

CATALOG_DIR = Path(__file__).resolve().parent
CATALOG_PATH = CATALOG_DIR / "catalog.json"
CHECKSUMS_PATH = CATALOG_DIR / "SHA256SUMS"
AUDIO_DIR = CATALOG_DIR / "audio"


def expected_sha256() -> str:
    """Read the archive's checksum from SHA256SUMS. Lines starting with # are ignored."""
    for line in CHECKSUMS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        if name.strip() == ARCHIVE_NAME:
            return digest
    raise SystemExit(
        f"No entry for {ARCHIVE_NAME} in {CHECKSUMS_PATH}. Cannot verify the download."
    )


def download(url: str, target: Path) -> str:
    """Download `url` to `target` and return the SHA-256 of what was written."""
    sha256 = hashlib.sha256()
    with urllib.request.urlopen(url) as response, target.open("wb") as out:
        while chunk := response.read(1 << 16):
            sha256.update(chunk)
            out.write(chunk)
    return sha256.hexdigest()


def main() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if all(track.get("placeholder") for track in catalog["tracks"]):
        print("Catalog contains only placeholder tracks. Generating local test audio with ffmpeg.")
        generate_placeholder_audio()
        return

    expected = expected_sha256()
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / ARCHIVE_NAME
        print(f"Downloading {ARCHIVE_URL}")
        actual = download(ARCHIVE_URL, archive)
        if actual != expected:
            print(
                f"SHA-256 mismatch for {ARCHIVE_NAME}.\n"
                f"  expected {expected}\n"
                f"  got      {actual}\n"
                "The download was not unpacked.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(AUDIO_DIR)
        print(f"Unpacked {ARCHIVE_NAME} into {AUDIO_DIR}")


if __name__ == "__main__":
    main()
