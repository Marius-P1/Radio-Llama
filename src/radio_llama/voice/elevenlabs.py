"""ElevenLabs backend on the official SDK: text to speech, and the account's voice list."""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from elevenlabs import ElevenLabs, VerifiedVoiceLanguageResponseModel, VoiceSettings
from elevenlabs import Voice as AccountVoice
from elevenlabs.core.api_error import ApiError
from elevenlabs.core.request_options import RequestOptions

from radio_llama.voice.base import TTSError, Voice, VoiceOption

log = logging.getLogger(__name__)

# How long the voice list is kept in memory. The account's voices rarely change.
VOICE_LIST_SECONDS = 600.0
# The most voices ElevenLabs returns in one page.
PAGE_SIZE = 100
# A person waits on the create screen while the voices are listed, so that call gives up
# after ten seconds and is not tried again. The SDK's defaults are 240 seconds and two
# more tries after a 5xx or 429 answer, each after a pause of up to a minute. The TTS
# client keeps those defaults: voicing a segment may take long, and it runs in the
# background job, where waiting and trying again is right.
VOICE_LIST_OPTIONS: RequestOptions = {"timeout_in_seconds": 10, "max_retries": 0}


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


class ElevenLabsVoices:
    """The voices of the ElevenLabs account behind the API key (D040).

    The list is read page by page and kept in memory for ten minutes, so opening the
    create screen does not call ElevenLabs every time. Listing voices spends no credit.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        client: ElevenLabs | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # `client` and `clock` are for tests; production uses the real ones.
        self._client = client if client is not None else ElevenLabs(api_key=api_key)
        # The model the shows are voiced with: its samples are preferred.
        self._model_id = model_id
        self._clock = clock
        self._voices: list[VoiceOption] | None = None
        self._fetched_at = 0.0

    def list_voices(self) -> list[VoiceOption]:
        # There is no lock. The routes run in a thread pool, so two requests that find
        # the list missing or expired may both call ElevenLabs. That is harmless: each
        # call builds a complete list of its own before storing it, and the last one
        # stored wins.
        now = self._clock()
        if self._voices is not None and now - self._fetched_at < VOICE_LIST_SECONDS:
            return list(self._voices)
        # A failure raises here, before anything is stored, so it is never kept: the
        # next call asks ElevenLabs again.
        voices = self._fetch()
        self._voices = voices
        self._fetched_at = now
        # A copy, like the fake list gives: a caller cannot change the kept list.
        return list(voices)

    def _fetch(self) -> list[VoiceOption]:
        found: list[AccountVoice] = []
        token: str | None = None
        try:
            while True:
                page = self._client.voices.search(
                    page_size=PAGE_SIZE, next_page_token=token, request_options=VOICE_LIST_OPTIONS
                )
                found.extend(page.voices)
                token = page.next_page_token
                if not page.has_more or token is None:
                    break
        except ApiError as exc:
            # The error shown by the page and the CLI is a short sentence. ElevenLabs' own
            # answer is text this project does not control, so it is logged as a warning
            # instead: the server log for the API, stderr in the terminal for the CLI.
            log.warning("ElevenLabs voice list failed: HTTP %s %s", exc.status_code, exc.body)
            raise TTSError(
                f"Could not list the ElevenLabs voices (HTTP {exc.status_code})."
            ) from exc
        except Exception as exc:
            # No HTTP answer at all: a timeout, no network, a reply the SDK cannot read.
            log.warning("ElevenLabs voice list failed: %r", exc)
            raise TTSError(
                "Could not list the ElevenLabs voices: no answer from ElevenLabs."
            ) from exc
        return [to_voice_option(voice, self._model_id) for voice in found]


def to_voice_option(voice: AccountVoice, model_id: str) -> VoiceOption:
    """Map one voice of the SDK to what the picker shows.

    Sample URLs go to the browser exactly as ElevenLabs gives them, query string included.
    Most sit on public storage. Some are signed links on an ElevenLabs API host
    (`/v1/voices/<id>/previews/audio?payload=<signed token>`): they play without the API
    key, and the token in them is not the key. The key itself is never part of a voice.
    """
    labels = voice.labels or {}
    entries = voice.verified_languages or []
    # A voice has one entry per language and model. `dict.fromkeys` keeps each language
    # once, in the order ElevenLabs gives them.
    languages = list(dict.fromkeys(entry.language for entry in entries))
    previews: dict[str, str] = {}
    for language in languages:
        sample = _sample([entry for entry in entries if entry.language == language], model_id)
        if sample is not None:
            previews[language] = sample
    return VoiceOption(
        id=voice.voice_id,
        name=voice.name or voice.voice_id,
        gender=labels.get("gender") or None,
        accent=labels.get("accent") or None,
        age=labels.get("age") or None,
        descriptive=labels.get("descriptive") or None,
        language=labels.get("language") or None,
        languages=languages,
        preview_url=voice.preview_url or None,
        previews=previews,
    )


def _sample(entries: list[VerifiedVoiceLanguageResponseModel], model_id: str) -> str | None:
    """One language's sample: the one made with the model the shows use, else the first."""
    samples = [entry for entry in entries if entry.preview_url]
    for entry in samples:
        if entry.model_id == model_id:
            return entry.preview_url
    return samples[0].preview_url if samples else None
