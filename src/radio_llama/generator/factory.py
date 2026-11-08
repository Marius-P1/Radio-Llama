"""Build the generator named in the settings. Only the pipeline exists in Phase 1 (D008)."""

from radio_llama.config import Settings
from radio_llama.generator.pipeline import PipelineGenerator
from radio_llama.llm.factory import make_llm_client
from radio_llama.music import MusicSource, make_music_sources
from radio_llama.voice.factory import make_tts


def make_generator(
    settings: Settings, sources: dict[str, MusicSource] | None = None
) -> PipelineGenerator:
    """`sources` lets the API share its music sources with the generator (D029)."""
    if sources is None:
        sources = make_music_sources(settings)
    return PipelineGenerator(make_llm_client(settings), make_tts(settings), sources, settings)
