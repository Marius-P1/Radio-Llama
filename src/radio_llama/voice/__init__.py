"""Text-to-speech: one interface, a real backend, a fake one and a disk cache."""

from radio_llama.voice.base import TextToSpeech, TTSError, Voice
from radio_llama.voice.cache import CachedTextToSpeech, cache_key
from radio_llama.voice.elevenlabs import ElevenLabsTTS
from radio_llama.voice.fake import FakeTTS

__all__ = [
    "CachedTextToSpeech",
    "ElevenLabsTTS",
    "FakeTTS",
    "TTSError",
    "TextToSpeech",
    "Voice",
    "cache_key",
]
