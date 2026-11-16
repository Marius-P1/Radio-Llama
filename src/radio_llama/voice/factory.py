"""Build the text-to-speech backend named in the settings, always behind the disk cache,
and the voice list that goes with it."""

from radio_llama.config import Settings
from radio_llama.models import Persona
from radio_llama.voice.base import TextToSpeech, Voice, VoiceList
from radio_llama.voice.cache import CachedTextToSpeech
from radio_llama.voice.elevenlabs import ElevenLabsTTS, ElevenLabsVoices
from radio_llama.voice.fake import FakeTTS, FakeVoices


def make_tts(settings: Settings) -> TextToSpeech:
    work_dir = settings.cache_dir / "tts-raw"
    backend: TextToSpeech
    if settings.tts_backend == "fake":
        backend = FakeTTS(work_dir)
    elif settings.tts_backend == "elevenlabs":
        backend = ElevenLabsTTS(settings.require_api_key("elevenlabs"), work_dir)
    else:
        raise ValueError(f"Unknown TTS backend: {settings.tts_backend!r}")
    return CachedTextToSpeech(backend, settings.cache_dir)


def make_voice_list(settings: Settings) -> VoiceList:
    """The voices the listener can pick from, for the same backend as `make_tts` (D040)."""
    if settings.tts_backend == "fake":
        return FakeVoices()
    if settings.tts_backend == "elevenlabs":
        return ElevenLabsVoices(
            settings.require_api_key("elevenlabs"), settings.elevenlabs_model_id
        )
    raise ValueError(f"Unknown TTS backend: {settings.tts_backend!r}")


def voice_for(persona: Persona, settings: Settings, voice_id: str | None = None) -> Voice:
    """The voice the listener picked, or the persona's own when `voice_id` is None (D040)."""
    return Voice(voice_id=voice_id or persona.voice_id, model_id=settings.elevenlabs_model_id)
