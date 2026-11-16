"""The Phase 1 generator: a fixed template of intro, songs, transitions and outro."""

import random
from datetime import datetime
from pathlib import Path

from radio_llama.audio.assemble import DEMO_SONG_LENGTH_S, assemble_show, write_speech
from radio_llama.config import Settings
from radio_llama.facts import FactLookup
from radio_llama.generator.base import GenerationError, ProgressCallback
from radio_llama.llm.base import LLMClient, LLMResponse
from radio_llama.models import ENERGIES, Energy, Fact, Segment, Show, ShowConfig, Track
from radio_llama.music import (
    JamendoSource,
    MusicSource,
    YouTubeQuotaError,
    YouTubeSource,
    missing_source_message,
)
from radio_llama.personas import get_persona
from radio_llama.script.playlist import propose_playlist
from radio_llama.script.shapes import Shape, load_shapes, plan_shapes
from radio_llama.script.wish import map_wish
from radio_llama.script.writer import ScriptWriter
from radio_llama.trace import TRACE_DIR, script_trace_dir, tracing
from radio_llama.voice.base import TextToSpeech
from radio_llama.voice.factory import voice_for

# Facts kept per track once the looked-up ones are added. The host uses at most two.
MAX_TRACK_FACTS = 5


class PipelineGenerator:
    def __init__(
        self,
        llm: LLMClient,
        tts: TextToSpeech,
        sources: dict[str, MusicSource],
        settings: Settings,
        facts: FactLookup | None = None,
    ) -> None:
        self._llm = llm
        self._tts = tts
        self._sources = sources
        self._settings = settings
        # Looks up facts for online tracks on Wikipedia and MusicBrainz (D029). None: no lookup.
        self._facts = facts
        # Every LLM response of the last run, so the CLI can print token totals (D004).
        self.last_responses: list[LLMResponse] = []

    def generate(
        self, show: Show, out_dir: Path, on_progress: ProgressCallback | None = None
    ) -> Show:
        # Every API call of the run is recorded under the show folder (D037).
        with tracing(out_dir / TRACE_DIR):
            return self._generate(show, out_dir, on_progress)

    def _generate(self, show: Show, out_dir: Path, on_progress: ProgressCallback | None) -> Show:
        music, tracks, plan, shapes, writer = self._prepare(show, on_progress)
        embed = music.delivery == "embed"  # songs play in the web app, not from a file (D038)

        # Every audio file present, fetched first for a remote source, before any LLM call.
        if not embed:
            for track in tracks:
                path = music.audio_path(track)
                if not path.exists():
                    raise GenerationError(
                        f"Audio file missing: {path}. Run `uv run python catalog/download.py`."
                    )
        voice = voice_for(writer.persona, self._settings)

        # Write and voice each speech segment, point each song at its audio file. An embed
        # show has no song file: the segment only carries how long the video plays.
        total = len(plan)
        demo = show.config.demo
        sources: dict[int, Path] = {}  # raw audio per segment index
        for index, segment in enumerate(plan):
            if segment.kind == "song":
                track = _track_by_id(tracks, segment.track_id)
                if embed:
                    segment.duration_s = DEMO_SONG_LENGTH_S if demo else track.duration_s
                else:
                    sources[index] = music.audio_path(track)
            else:
                segment.text = _write_speech(writer, segment, tracks, shapes)
                sources[index] = self._tts.synthesize(segment.text, voice)
            if on_progress is not None:
                on_progress(index + 1, total)

        # Build the audio; this fills audio_file and duration_s. Status and show.json
        # belong to the lifecycle owner.
        show.tracks = tracks
        show.segments = plan
        if embed:
            write_speech(show, sources, out_dir)
        else:
            assemble_show(show, [sources[index] for index in range(total)], out_dir)
        self.last_responses.extend(writer.responses)
        return show

    def write_script(self, show: Show) -> Show:
        """Pick the tracks and write every spoken segment, with no voice and no audio.

        For the `radio-llama script` command: reading ten scripts costs cents, voicing
        them does not (D033). With no show folder, the trace goes under the output
        folder's `scripts/` (D037).
        """
        with tracing(script_trace_dir(self._settings.output_dir, show.id)):
            _, tracks, plan, shapes, writer = self._prepare(show)
            for segment in plan:
                if segment.kind != "song":
                    segment.text = _write_speech(writer, segment, tracks, shapes)
        show.tracks = tracks
        show.segments = plan
        self.last_responses.extend(writer.responses)
        return show

    def _prepare(
        self, show: Show, on_progress: ProgressCallback | None = None
    ) -> tuple[MusicSource, list[Track], list[Segment], dict[str, Shape], ScriptWriter]:
        """Pick the tracks and the break shapes, build the plan and the writer.

        `on_progress` gets (0, total) as soon as the tracks are picked, before the facts
        lookups, so the page shows the total while the network is slow.
        """
        config = show.config
        music = self._sources.get(config.source)
        if music is None:
            raise GenerationError(missing_source_message(config.source))

        # A seeded random sample of the candidates (D017), the listener's energy first
        # (D020); the same generator then picks the break shapes (D033).
        self.last_responses = []
        candidates = self._candidates(config, music)
        if len(candidates) < config.track_count:
            raise GenerationError(
                f"Style {config.style!r} has {len(candidates)} tracks, {config.track_count} needed."
            )
        rng = random.Random(config.seed)
        tracks = pick_tracks(candidates, config.track_count, config.energy, rng)
        if on_progress is not None:
            on_progress(0, plan_length(len(tracks)))
        # Before the shapes: a track that gains facts can get the story break.
        lookup = self._facts
        if lookup is not None and music.needs_facts_lookup:
            tracks = [
                _add_facts(track, lookup.artist_facts(track.artist, config.language))
                for track in tracks
            ]
        shapes = load_shapes(self._settings.prompts_dir, config.language)
        breaks = plan_shapes(shapes, [bool(track.facts) for track in tracks[1:]], rng)
        plan = _plan(tracks, breaks)

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
            now=datetime.now(self._settings.tzinfo),
        )
        return music, tracks, plan, {shape.id: shape for shape in shapes}, writer

    def _candidates(self, config: ShowConfig, music: MusicSource) -> list[Track]:
        """The tracks to sample from, one search per source.

        Jamendo searches with the energy and the wish (D030). YouTube asks the model for
        songs, two more than needed, and keeps those with a playable video (D038). The
        catalog lists its tracks.
        """
        if config.wish and not music.accepts_wish:
            raise GenerationError("A wish needs the 'jamendo' or 'youtube' music source.")
        if isinstance(music, YouTubeSource):
            proposals, response = propose_playlist(
                self._llm,
                self._settings.prompts_dir,
                config.language,
                style=config.style,
                energy=config.energy,
                wish=config.wish,
                count=config.track_count + 2,
            )
            self.last_responses.append(response)
            try:
                return music.find_tracks(proposals, style=config.style, minimum=config.track_count)
            except YouTubeQuotaError as exc:
                raise GenerationError(str(exc)) from exc
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


def _add_facts(track: Track, found: list[Fact]) -> Track:
    """A copy of `track` with the looked-up facts after its own.

    A copy, so the music source keeps the track as Jamendo described it.
    """
    return track.model_copy(update={"facts": merge_facts(track.facts, found)})


def merge_facts(own: list[Fact], found: list[Fact]) -> list[Fact]:
    """`own` then `found`, without a sentence twice, at most MAX_TRACK_FACTS."""
    merged: list[Fact] = []
    seen: set[str] = set()
    for fact in [*own, *found]:
        key = fact.text.casefold()
        if key not in seen:
            seen.add(key)
            merged.append(fact)
    return merged[:MAX_TRACK_FACTS]


def plan_length(track_count: int) -> int:
    """How many segments `_plan` makes: intro, the songs, a break between each two, outro."""
    return 2 * track_count + 1


def _plan(tracks: list[Track], breaks: list[Shape]) -> list[Segment]:
    """intro, song, transition, song, ..., song, outro.

    A transition names the next track and carries the shape of its break (D033).
    """
    plan = [Segment(kind="intro")]
    for index, track in enumerate(tracks):
        plan.append(Segment(kind="song", track_id=track.id))
        if index < len(tracks) - 1:
            plan.append(
                Segment(kind="transition", track_id=tracks[index + 1].id, shape=breaks[index].id)
            )
    plan.append(Segment(kind="outro"))
    return plan


def _write_speech(
    writer: ScriptWriter, segment: Segment, tracks: list[Track], shapes: dict[str, Shape]
) -> str:
    if segment.kind == "intro":
        return writer.write_intro(tracks)
    if segment.kind == "outro":
        return writer.write_outro(tracks)
    next_track = _track_by_id(tracks, segment.track_id)
    previous = tracks[tracks.index(next_track) - 1]
    if segment.shape is None:
        raise ValueError("A transition segment needs a shape.")
    return writer.write_transition(previous, next_track, shapes[segment.shape])


def _track_by_id(tracks: list[Track], track_id: str | None) -> Track:
    for track in tracks:
        if track.id == track_id:
            return track
    raise ValueError(f"No picked track with id {track_id!r}")
