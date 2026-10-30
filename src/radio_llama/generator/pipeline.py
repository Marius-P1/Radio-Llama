"""The Phase 1 generator: a fixed template of intro, songs, transitions and outro."""

import random
from pathlib import Path

from radio_llama.audio.assemble import assemble_show
from radio_llama.config import Settings
from radio_llama.generator.base import GenerationError, ProgressCallback
from radio_llama.llm.base import LLMClient, LLMResponse
from radio_llama.models import Segment, Show, Track
from radio_llama.music.local_catalog import LocalCatalog
from radio_llama.personas import get_persona
from radio_llama.script.writer import ScriptWriter
from radio_llama.voice.base import TextToSpeech
from radio_llama.voice.factory import voice_for


class PipelineGenerator:
    def __init__(
        self, llm: LLMClient, tts: TextToSpeech, music: LocalCatalog, settings: Settings
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._music = music
        self._settings = settings
        # Every LLM response of the last run, so the CLI can print token totals (D004).
        self.last_responses: list[LLMResponse] = []

    def generate(
        self, show: Show, out_dir: Path, on_progress: ProgressCallback | None = None
    ) -> Show:
        config = show.config

        # Pick the tracks: a seeded random sample of the style, every audio file present (D017).
        candidates = self._music.list_tracks(config.style)
        if len(candidates) < config.track_count:
            raise GenerationError(
                f"Style {config.style!r} has {len(candidates)} tracks, {config.track_count} needed."
            )
        tracks = random.Random(config.seed).sample(candidates, config.track_count)
        for track in tracks:
            path = self._music.audio_path(track)
            if not path.exists():
                raise GenerationError(
                    f"Audio file missing: {path}. Run `uv run python catalog/download.py`."
                )

        # The host: persona text for the prompts, voice for the TTS.
        persona = get_persona(self._settings.prompts_dir, config.language, config.persona)
        writer = ScriptWriter(
            self._llm, self._settings.prompts_dir, config.language, persona, config.style
        )
        voice = voice_for(persona, self._settings)

        # The plan: intro, then each song followed by a transition to the next, then outro.
        plan = _plan(tracks)
        total = len(plan)

        # Write and voice each speech segment, point each song at its catalog file.
        sources: list[Path] = []
        for done, segment in enumerate(plan, start=1):
            if segment.kind == "song":
                sources.append(self._music.audio_path(_track_by_id(tracks, segment.track_id)))
            else:
                segment.text = _write_speech(writer, segment, tracks)
                sources.append(self._tts.synthesize(segment.text, voice))
            if on_progress is not None:
                on_progress(done, total)

        # Assemble the audio; this fills audio_file and duration_s. Status and show.json
        # belong to the lifecycle owner.
        show.tracks = tracks
        show.segments = plan
        assemble_show(show, sources, out_dir)
        self.last_responses = writer.responses
        return show


def _plan(tracks: list[Track]) -> list[Segment]:
    """intro, song, transition, song, ..., song, outro. A transition names the next track."""
    plan = [Segment(kind="intro")]
    for index, track in enumerate(tracks):
        plan.append(Segment(kind="song", track_id=track.id))
        if index < len(tracks) - 1:
            plan.append(Segment(kind="transition", track_id=tracks[index + 1].id))
    plan.append(Segment(kind="outro"))
    return plan


def _write_speech(writer: ScriptWriter, segment: Segment, tracks: list[Track]) -> str:
    if segment.kind == "intro":
        return writer.write_intro(tracks)
    if segment.kind == "outro":
        return writer.write_outro(tracks)
    next_track = _track_by_id(tracks, segment.track_id)
    previous = tracks[tracks.index(next_track) - 1]
    return writer.write_transition(previous, next_track)


def _track_by_id(tracks: list[Track], track_id: str | None) -> Track:
    for track in tracks:
        if track.id == track_id:
            return track
    raise ValueError(f"No picked track with id {track_id!r}")
