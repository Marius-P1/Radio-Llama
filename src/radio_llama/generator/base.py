"""The ShowGenerator interface (D008, D017) shared by the pipeline and, later, the agent."""

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

        The caller creates the Show and owns its status and `show.json`.
        """
        ...
