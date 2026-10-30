"""Pick the LLM adapter named in Settings."""

from radio_llama.config import Settings
from radio_llama.llm.base import LLMClient
from radio_llama.llm.fake import FakeLLM


def make_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeLLM()
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")
