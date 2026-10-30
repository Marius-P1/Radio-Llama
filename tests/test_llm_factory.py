from pathlib import Path

import pytest

from radio_llama.config import ConfigError, Settings
from radio_llama.llm import AnthropicClient, FakeLLM, make_llm_client


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in an empty folder with none of our variables set."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_fake_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    assert isinstance(make_llm_client(Settings()), FakeLLM)


def test_mistral_without_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    with pytest.raises(ConfigError, match="MISTRAL_API_KEY"):
        make_llm_client(Settings())


def test_anthropic_without_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_llm_client(Settings())


def test_anthropic_with_key_returns_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    # anthropic.Anthropic(api_key=...) only builds an HTTP client; it makes no request.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert isinstance(make_llm_client(Settings()), AnthropicClient)
