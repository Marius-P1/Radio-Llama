"""Text-to-speech: one interface, a real backend and a fake one."""

from radio_llama.voice.base import TextToSpeech, TTSError, Voice
from radio_llama.voice.elevenlabs import ElevenLabsTTS
from radio_llama.voice.fake import FakeTTS

__all__ = ["ElevenLabsTTS", "FakeTTS", "TTSError", "TextToSpeech", "Voice"]
