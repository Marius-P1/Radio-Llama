"""Read and write `show.json`, and mint show ids (D011: shows are files, no database)."""

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from radio_llama.models import Show

SHOW_FILE = "show.json"


def save_show(show: Show, out_dir: Path) -> Path:
    """Write the show to `out_dir / show.json`, creating the folder, and return the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / SHOW_FILE
    path.write_text(show.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def load_show(out_dir: Path) -> Show:
    """Read and validate `out_dir / show.json`."""
    return Show.model_validate_json((out_dir / SHOW_FILE).read_text(encoding="utf-8"))


def new_show_id(now: datetime) -> str:
    """A sortable id such as `20261030-181530-3fa9c2`: timestamp plus a short random suffix."""
    return f"{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
