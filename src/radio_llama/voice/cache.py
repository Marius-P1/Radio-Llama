"""Disk cache for text-to-speech outputs.

The key is the SHA-256 of a canonical JSON of the text, voice id, model id and voice settings,
so anything that changes the audio changes the key. Files live at `<cache_dir>/tts/<key>.mp3`
and the filesystem is the whole index: a file that exists is a hit. The cache wraps the
`TextToSpeech` protocol instead of living inside a backend, so any backend, real or fake, gets
caching for free.
"""

import hashlib
import json
import logging
from pathlib import Path

from radio_llama.voice.base import TextToSpeech, Voice

log = logging.getLogger(__name__)


def cache_key(text: str, voice: Voice) -> str:
    payload = {
        "text": text,
        "voice_id": voice.voice_id,
        "model_id": voice.model_id,
        "settings": voice.settings,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CachedTextToSpeech:
    def __init__(self, inner: TextToSpeech, cache_dir: Path) -> None:
        self._inner = inner
        self._dir = cache_dir / "tts"

    @property
    def inner(self) -> TextToSpeech:
        return self._inner

    def synthesize(self, text: str, voice: Voice) -> Path:
        key = cache_key(text, voice)
        final = self._dir / f"{key}.mp3"
        if final.exists():
            log.info("TTS cache hit %s", key[:12])
            return final
        log.info("TTS cache miss %s", key[:12])
        raw = self._inner.synthesize(text, voice)
        self._dir.mkdir(parents=True, exist_ok=True)
        # Same filesystem as the backend's work dir, so this is a rename, not a copy.
        raw.replace(final)
        return final
