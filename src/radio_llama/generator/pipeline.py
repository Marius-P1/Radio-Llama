"""The Phase 1 generator: a fixed template of intro, songs, transitions and outro."""

import random
from pathlib import Path

from radio_llama.audio.assemble import assemble_show
from radio_llama.config import Settings
from radio_llama.generator.base import GenerationError, ProgressCallback
from radio_llama.llm.base import LLMClient, LLMResponse
from radio_llama.models import ENERGIES, Energy, Segment, Show, ShowConfig, Track
from radio_llama.music import JamendoSource, MusicSource, missing_source_message
from radio_llama.personas import get_persona
from radio_llama.script.wish import map_wish
from radio_llama.script.writer import ScriptWriter
from radio_llama.voice.base import TextToSpeech
from radio_llama.voice.factory import voice_for


class PipelineGenerator:
    def __init__(
        self,
        llm: LLMClient,
        tts: TextToSpeech,
        sources: dict[str, MusicSource],
        settings: Settings,
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._sources = sources
        self._settings = settings
        # Every LLM response of the last run, so the CLI can print token totals (D004).
        self.last_responses: list[LLMResponse] = []

    def generate(
        self, show: Show, out_dir: Path, on_progress: ProgressCallback | None = None
    ) -> Show:
        config = show.config
        music = self._sources.get(config.source)
        if music is None:
            raise GenerationError(missing_source_message(config.source))

        # Pick the tracks: a seeded random sample of the candidates (D017), the listener's
        # energy first (D020), every audio file present (fetched first for a remote source).
        self.last_responses = []
        candidates = self._candidates(config, music)
        if len(candidates) < config.track_count:
            raise GenerationError(
                f"Style {config.style!r} has {len(candidates)} tracks, {config.track_count} needed."
            )
        tracks = pick_tracks(
            candidates, config.track_count, config.energy, random.Random(config.seed)
        )
        for track in tracks:
            path = music.audio_path(track)
            if not path.exists():
                raise GenerationError(
                    f"Audio file missing: {path}. Run `uv run python catalog/download.py`."
                )

        # The host: persona text for the prompts, voice for the TTS.
        persona = get_persona(self._settings.prompts_dir, config.language, config.persona)
        writer = ScriptWriter(
            self._llm,
            self._settings.prompts_dir,
            config.language,
            persona,
            config.style,
            energy=config.energy,
            wish=config.wish,
        )
        voice = voice_for(persona, self._settings)

        # The plan: intro, then each song followed by a transition to the next, then outro.
        plan = _plan(tracks)
        total = len(plan)

        # Write and voice each speech segment, point each song at its audio file.
        sources: list[Path] = []
        for done, segment in enumerate(plan, start=1):
            if segment.kind == "song":
                sources.append(music.audio_path(_track_by_id(tracks, segment.track_id)))
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
        self.last_responses.extend(writer.responses)
        return show

    def _candidates(self, config: ShowConfig, music: MusicSource) -> list[Track]:
        """The tracks to sample from. Jamendo searches with the energy and the wish (D030)."""
        if isinstance(music, JamendoSource):
            filters = None
            if config.wish:
                filters, response = map_wish(
                    self._llm,
                    self._settings.prompts_dir,
                    config.language,
                    wish=config.wish,
                    style=config.style,
                    energy=config.energy,
                )
                self.last_responses.append(response)
            return music.find_tracks(
                config.style, energy=config.energy, filters=filters, minimum=config.track_count
            )
        if config.wish:
            raise GenerationError("A wish needs the 'jamendo' music source.")
        return music.list_tracks(config.style)


def pick_tracks(
    candidates: list[Track], count: int, energy: Energy | None, rng: random.Random
) -> list[Track]:
    """A seeded random sample of `count` tracks, by tiers when an energy is asked for (D020).

    Tiers: the requested energy, then tracks with no energy, then the other energies nearest
    first (medium sits between calm and upbeat). Each tier is sampled at random until
    `count` is reached. With no energy, one sample over every candidate.
    """
    if energy is None:
        return rng.sample(candidates, count)

    def tier(track: Track) -> int:
        if track.energy is None:
            return 1
        distance = abs(ENERGIES.index(track.energy) - ENERGIES.index(energy))
        return 0 if distance == 0 else distance + 1

    picked: list[Track] = []
    for rank in range(len(ENERGIES) + 1):
        tracks = [track for track in candidates if tier(track) == rank]
        picked.extend(rng.sample(tracks, min(count - len(picked), len(tracks))))
    return picked


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
