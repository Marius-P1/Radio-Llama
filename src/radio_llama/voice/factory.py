"""Build the text-to-speech backend named in the settings, always behind the disk cache."""

from radio_llama.config import Settings
from radio_llama.models import Persona
from radio_llama.voice.base import TextToSpeech, Voice
from radio_llama.voice.cache import CachedTextToSpeech
from radio_llama.voice.elevenlabs import ElevenLabsTTS
from radio_llama.voice.fake import FakeTTS


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


def voice_for(persona: Persona, settings: Settings) -> Voice:
    return Voice(voice_id=persona.voice_id, model_id=settings.elevenlabs_model_id)
