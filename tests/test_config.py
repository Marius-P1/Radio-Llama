import re
from pathlib import Path

import pytest

from radio_llama.config import ConfigError, Settings, load_settings

ENV_EXAMPLE = Path(__file__).resolve().parent.parent / ".env.example"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in an empty folder with none of our variables set."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults_load_without_env() -> None:
    settings = load_settings()
    assert settings.radio_language == "en"
    assert settings.llm_provider == "mistral"
    assert settings.llm_model == "mistral-small-latest"
    assert settings.tts_backend == "elevenlabs"
    assert settings.cache_dir == Path("cache")
    assert settings.mistral_api_key is None


def test_require_api_key_missing_names_the_variable() -> None:
    settings = load_settings()
    with pytest.raises(ConfigError, match="MISTRAL_API_KEY"):
        settings.require_api_key("mistral")


def test_require_api_key_returns_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "secret-value")
    assert load_settings().require_api_key("mistral") == "secret-value"


def test_require_api_key_unknown_provider() -> None:
    with pytest.raises(ValueError, match="openai"):
        load_settings().require_api_key("openai")


def test_invalid_language_gives_one_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADIO_LANGUAGE", "de")
    with pytest.raises(ConfigError) as info:
        load_settings()
    message = str(info.value)
    assert "RADIO_LANGUAGE" in message
    assert "'de'" in message
    assert "Traceback" not in message
    assert len(message.splitlines()) == 1


def test_env_example_lists_every_setting() -> None:
    names = set(re.findall(r"^([A-Z_]+)=$", ENV_EXAMPLE.read_text(encoding="utf-8"), re.MULTILINE))
    expected = {name.upper() for name in Settings.model_fields}
    assert names == expected
