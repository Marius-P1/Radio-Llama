"""Run a generator and own the show's status and `show.json`.

The CLI and the API (P1-10) both generate shows, and P1-10 runs the generation in a
background task where an uncaught exception would vanish; one function that moves the
status from generating to ready or failed, and saves the file at each step, means both
callers behave the same and a failure always leaves a readable `show.json` behind. The
generator itself only produces segments and audio, so it never needs to know who is
calling it or where the show is stored.
"""

import logging
from pathlib import Path

from radio_llama.audio.ffmpeg import AudioError
from radio_llama.config import ConfigError
from radio_llama.generator.base import GenerationError, ProgressCallback, ShowGenerator
from radio_llama.llm.base import LLMError
from radio_llama.models import Show
from radio_llama.music import MusicSourceError
from radio_llama.storage import save_show
from radio_llama.voice.base import TTSError

log = logging.getLogger(__name__)

# Expected failures: they end as status "failed" with a message. Anything else is a bug
# and propagates.
EXPECTED_ERRORS = (
    GenerationError,
    LLMError,
    TTSError,
    AudioError,
    MusicSourceError,
    ConfigError,
    ValueError,
    OSError,
)


def run_generation(
    generator: ShowGenerator,
    show: Show,
    out_dir: Path,
    on_progress: ProgressCallback | None = None,
) -> Show:
    """Generate `show` into `out_dir`, saving `show.json` before, after, and on failure."""
    show.status = "generating"
    save_show(show, out_dir)
    try:
        generator.generate(show, out_dir, on_progress)
    except EXPECTED_ERRORS as exc:
        log.exception("Show %s failed", show.id)
        show.status = "failed"
        show.error = str(exc)
        save_show(show, out_dir)
        return show
    show.status = "ready"
    save_show(show, out_dir)
    return show
