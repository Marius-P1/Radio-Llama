"""Text-to-speech: one interface, a real backend, a fake one and a disk cache.
The same two backends list the voices the listener can pick from."""

from radio_llama.voice.base import (
    TextToSpeech,
    TTSError,
    UnknownVoiceError,
    Voice,
    VoiceList,
    VoiceOption,
    find_voice,
)
from radio_llama.voice.cache import CachedTextToSpeech, cache_key
from radio_llama.voice.elevenlabs import ElevenLabsTTS, ElevenLabsVoices
from radio_llama.voice.factory import make_tts, make_voice_list, voice_for
from radio_llama.voice.fake import FakeTTS, FakeVoices

__all__ = [
    "CachedTextToSpeech",
    "ElevenLabsTTS",
    "ElevenLabsVoices",
    "FakeTTS",
    "FakeVoices",
    "TTSError",
    "TextToSpeech",
    "UnknownVoiceError",
    "Voice",
    "VoiceList",
    "VoiceOption",
    "cache_key",
    "find_voice",
    "make_tts",
    "make_voice_list",
    "voice_for",
]
