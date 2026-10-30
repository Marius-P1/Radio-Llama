"""Build the generator named in the settings. Only the pipeline exists in Phase 1 (D008)."""

from radio_llama.config import Settings
from radio_llama.generator.pipeline import PipelineGenerator
from radio_llama.llm.factory import make_llm_client
from radio_llama.music.local_catalog import LocalCatalog
from radio_llama.voice.factory import make_tts


def make_generator(settings: Settings) -> PipelineGenerator:
    return PipelineGenerator(
        make_llm_client(settings),
        make_tts(settings),
        LocalCatalog(settings.catalog_path),
        settings,
    )
