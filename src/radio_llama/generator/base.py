"""The ShowGenerator interface (D008, D017, D044) shared by the pipeline and, later, the agent."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from radio_llama.models import Show

ProgressCallback = Callable[[int, int], None]  # (segments done, total)


class GenerationError(Exception):
    """The show cannot be generated. The message is readable (missing audio, too few tracks...)."""


class ShowGenerator(Protocol):
    def generate(
        self, show: Show, out_dir: Path, on_progress: ProgressCallback | None = None
    ) -> Show:
        """Fill `show.tracks`, `show.segments` and the audio in `out_dir`, then return the show.

        The caller creates the Show and owns its status and `show.json`. For an endless
        show (`show.config.endless`, D044) this makes the opening only: the intro and the
        first song. `extend` adds the rest.
        """
        ...

    def extend(self, show: Show, out_dir: Path, *, end: bool = False) -> Show:
        """One more step of an endless show (D044), then return the show.

        Appends one break and one song. Appends the closing instead when `end` is true,
        or when the show has to end by itself, in which case `show.end_note` says why.
        `show` is the show as `show.json` holds it; the caller saves it once the call
        returns. On an error nothing was saved, and the show object may hold a part that
        was not finished: the caller drops it and reads `show.json` again.
        """
        ...
