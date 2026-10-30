from pathlib import Path

import pytest

from radio_llama.config import ConfigError, Settings, load_settings
from radio_llama.models import Persona
from radio_llama.voice import CachedTextToSpeech, FakeTTS, Voice, make_tts, voice_for


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in an empty folder with none of our variables set, so no .env is read."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_fake_backend_is_wrapped_in_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_BACKEND", "fake")
    tts = make_tts(load_settings())
    assert isinstance(tts, CachedTextToSpeech)
    assert isinstance(tts.inner, FakeTTS)


def test_elevenlabs_without_key_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_BACKEND", "elevenlabs")
    with pytest.raises(ConfigError, match="ELEVENLABS_API_KEY"):
        make_tts(load_settings())


def test_voice_for_combines_persona_voice_and_configured_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
    persona = Persona(id="calm", name="Calm", description="Slow and warm.", voice_id="abc")
    expected = Voice(voice_id="abc", model_id="eleven_flash_v2_5")
    assert voice_for(persona, load_settings()) == expected
