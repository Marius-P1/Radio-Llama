"""Pick the LLM adapter named in Settings."""

from radio_llama.config import Settings
from radio_llama.llm.anthropic import AnthropicClient
from radio_llama.llm.base import LLMClient
from radio_llama.llm.fake import FakeLLM
from radio_llama.llm.mistral import MistralClient


def make_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return FakeLLM()
    if settings.llm_provider == "mistral":
        return MistralClient(settings.require_api_key("mistral"), settings.llm_model)
    if settings.llm_provider == "anthropic":
        return AnthropicClient(settings.require_api_key("anthropic"), settings.llm_model)
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")
