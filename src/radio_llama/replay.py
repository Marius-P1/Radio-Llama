"""Export ready shows into the static layout the replay page reads (D010, D018).

The replay build of the frontend fetches `shows/index.json`, then `shows/<id>/show.json`
and the show's audio: `show.mp3` for a file show, one speech file per host segment for a
YouTube show, whose songs play in the embedded player (D043). An endless show the host
closed exports the same way, with one file per song as well when its songs are Jamendo
files (D044). This module fills that folder from `output/<id>/` and packs it into a zip
for the release asset, since audio is never committed (D002).
"""

import json
import shutil
import zipfile
from pathlib import Path

from radio_llama.api.schemas import ShowSummary
from radio_llama.models import Show
from radio_llama.music.factory import playback_of
from radio_llama.storage import SHOW_FILE, load_show

INDEX_FILE = "index.json"


def export_replay(show_ids: list[str], output_dir: Path, dest: Path) -> list[dict]:
    """Copy each ready show's `show.json` and audio into `dest / id /`, then rewrite the index.

    A file show brings its MP3, a show that plays by segments the file of each segment that
    has one. Returns one summary dict per exported show. Raises FileNotFoundError for an
    unknown id or a missing audio file and ValueError for a show that is not ready or is
    an endless show still open; nothing is copied for that id.
    """
    summaries = []
    for show_id in show_ids:
        source = output_dir / show_id
        if not (source / SHOW_FILE).exists():
            raise FileNotFoundError(f"No show {show_id} in {output_dir} (no {SHOW_FILE}).")
        show = load_show(source)
        if show.status != "ready":
            raise ValueError(
                f"Show {show_id} is {show.status}, not ready. Only ready shows can be exported."
            )
        # An endless show the listener left is ready but stops with no goodbye (D044).
        if show.is_open:
            raise ValueError(
                f"Show {show_id} has no closing. Only a show the host closed can be exported."
            )
        audio_files = _audio_files(show)
        for name in audio_files:
            if not (source / name).exists():
                raise FileNotFoundError(f"Show {show_id} has no audio file {name}.")

        target = dest / show_id
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / SHOW_FILE, target / SHOW_FILE)
        for name in audio_files:
            shutil.copyfile(source / name, target / name)
        summaries.append(_summary(show).model_dump(mode="json"))

    write_index(dest)
    return summaries


def write_index(dest: Path) -> Path:
    """Scan `dest/*/show.json` and write `dest/index.json`, newest show first."""
    shows = [load_show(path.parent) for path in sorted(dest.glob(f"*/{SHOW_FILE}"))]
    shows.sort(key=lambda show: (show.created_at, show.id), reverse=True)
    index = [_summary(show).model_dump(mode="json") for show in shows]
    path = dest / INDEX_FILE
    dest.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def zip_replay(dest: Path, zip_path: Path) -> Path:
    """Archive everything under `dest`, with paths relative to `dest`.

    The archive unpacks straight into `frontend/public/shows/`, which is what the Pages
    workflow does. The archive itself is skipped in case it is written inside `dest`.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(dest.rglob("*")):
            if path.is_file() and path.resolve() != zip_path.resolve():
                archive.write(path, arcname=path.relative_to(dest).as_posix())
    return zip_path


def _audio_files(show: Show) -> list[str]:
    """The files a ready show plays from: show.mp3, or one file per segment that has one.

    A show that plays by segments has no show.mp3. A YouTube show brings its speech
    files, since its songs are videos; an endless Jamendo show brings its song files
    too (D044).
    """
    if show.audio_file is not None:
        return [show.audio_file]
    if playback_of(show.config) != "segments":
        raise ValueError(f"Show {show.id} is ready but has no show.mp3.")
    return [segment.audio_file for segment in show.segments if segment.audio_file is not None]


def _summary(show: Show) -> ShowSummary:
    return ShowSummary(
        id=show.id,
        status=show.status,
        created_at=show.created_at,
        config=show.config,
        error=show.error,
    )
