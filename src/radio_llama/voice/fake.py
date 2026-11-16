"""A fake backend that writes a faint hum, so the whole pipeline runs without a paid API.
It also lists a few made-up voices, so the voice picker runs without one too.

It is a hum and not pure silence because loudness normalisation (ffmpeg loudnorm) produces
NaN samples on fully silent input and the MP3 encoder then aborts. Real speech is never silent.
"""

import subprocess
from pathlib import Path
from uuid import uuid4

from radio_llama.voice.base import TTSError, Voice, VoiceOption

MIN_SECONDS = 0.5

# Made-up voices for the picker (D040). They have no sample: the fake voice is a hum.
FAKE_VOICES = [
    VoiceOption(
        id="fake-voice-1",
        name="Ada",
        gender="female",
        accent="british",
        age="middle_aged",
        descriptive="warm",
        language="en",
        languages=["en", "fr"],
    ),
    VoiceOption(
        id="fake-voice-2",
        name="Basile",
        gender="male",
        accent="parisian",
        age="young",
        descriptive="lively",
        language="fr",
        languages=["fr"],
    ),
    VoiceOption(
        id="fake-voice-3",
        name="Cleo",
        gender="neutral",
        accent="american",
        age="young",
        descriptive="casual",
        language="en",
        languages=["en"],
    ),
    VoiceOption(
        id="fake-voice-4",
        name="Dorian",
        gender="male",
        accent="american",
        age="old",
        descriptive="deep",
        language="en",
        languages=["en", "fr"],
    ),
    VoiceOption(
        id="fake-voice-5",
        name="Elise",
        gender="female",
        accent="quebecois",
        age="middle_aged",
        descriptive="calm",
        language="fr",
        languages=["fr"],
    ),
]


class FakeTTS:
    def __init__(self, work_dir: Path, seconds_per_char: float = 0.06) -> None:
        self._work_dir = work_dir
        self._seconds_per_char = seconds_per_char
        self.calls = 0

    def synthesize(self, text: str, voice: Voice) -> Path:
        self.calls += 1
        self._work_dir.mkdir(parents=True, exist_ok=True)
        seconds = max(MIN_SECONDS, len(text) * self._seconds_per_char)
        path = self._work_dir / f"{uuid4().hex}.mp3"
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=200:sample_rate=44100",
            "-t",
            str(seconds),
            "-af",
            "volume=-30dB",
            "-ac",
            "2",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "9",
            "-y",
            str(path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            tail = exc.stderr.decode("utf-8", errors="replace").strip()[-500:]
            raise TTSError(f"Fake TTS: ffmpeg failed (exit {exc.returncode}): {tail}") from exc
        except OSError as exc:
            raise TTSError(f"Fake TTS: could not run ffmpeg: {exc}") from exc
        return path


class FakeVoices:
    """The voice list of the fake backend: a short fixed list, no network (D040)."""

    def list_voices(self) -> list[VoiceOption]:
        return list(FAKE_VOICES)
