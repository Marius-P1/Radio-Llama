"""ElevenLabs backend on the official SDK."""

from pathlib import Path
from uuid import uuid4

from elevenlabs import ElevenLabs, VoiceSettings

from radio_llama.voice.base import TTSError, Voice


class ElevenLabsTTS:
    def __init__(self, api_key: str, work_dir: Path, client: ElevenLabs | None = None) -> None:
        # `client` is for tests; production builds the real one from the key.
        self._client = client if client is not None else ElevenLabs(api_key=api_key)
        self._work_dir = work_dir

    def synthesize(self, text: str, voice: Voice) -> Path:
        self._work_dir.mkdir(parents=True, exist_ok=True)
        try:
            chunks = self._client.text_to_speech.convert(
                voice_id=voice.voice_id,
                text=text,
                model_id=voice.model_id,
                output_format="mp3_44100_128",
                voice_settings=VoiceSettings(**voice.settings) if voice.settings else None,
            )
            # The SDK streams the MP3 as an iterator of byte chunks.
            data = b"".join(chunks)
        except Exception as exc:
            raise TTSError(f"ElevenLabs call failed: {exc}") from exc
        path = self._work_dir / f"{uuid4().hex}.mp3"
        path.write_bytes(data)
        return path
