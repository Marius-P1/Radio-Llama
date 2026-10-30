"""Background generation: one show at a time, progress in memory (D011, D018)."""

import logging
import threading
from pathlib import Path

from radio_llama.api.schemas import Progress
from radio_llama.generator import ShowGenerator, run_generation
from radio_llama.models import Show
from radio_llama.storage import SHOW_FILE, load_show, save_show

log = logging.getLogger(__name__)

RESTARTED_ERROR = "Server restarted during generation."
UNEXPECTED_ERROR = "Unexpected error, see server log."


class Jobs:
    """Runs generations one at a time and remembers progress in memory (D011, D018)."""

    def __init__(self, generator: ShowGenerator, output_dir: Path) -> None:
        self._generator = generator
        self._output_dir = output_dir
        self._lock = threading.Lock()
        self._progress: dict[str, Progress] = {}

    def progress(self, show_id: str) -> Progress | None:
        return self._progress.get(show_id)

    def run(self, show: Show) -> None:
        """Generate `show`; a second call waits for the first to finish (one at a time)."""
        try:
            with self._lock:

                def on_progress(done: int, total: int) -> None:
                    self._progress[show.id] = Progress(done=done, total=total)

                try:
                    run_generation(self._generator, show, self._output_dir / show.id, on_progress)
                finally:
                    self._progress.pop(show.id, None)
        except Exception:
            # A bug must never leave a show stuck in "generating" or kill the worker thread.
            log.exception("Show %s crashed", show.id)
            show.status = "failed"
            show.error = UNEXPECTED_ERROR
            save_show(show, self._output_dir / show.id)


def mark_stale_shows_failed(output_dir: Path) -> int:
    """Fail every show left in `pending` or `generating` by a previous run. Returns the count."""
    count = 0
    if not output_dir.is_dir():
        return count
    for show_dir in output_dir.iterdir():
        if not (show_dir / SHOW_FILE).is_file():
            continue
        try:
            show = load_show(show_dir)
        except ValueError:
            log.warning("Skipping unreadable %s", show_dir / SHOW_FILE)
            continue
        if show.status in ("pending", "generating"):
            show.status = "failed"
            show.error = RESTARTED_ERROR
            save_show(show, show_dir)
            count += 1
    return count
