"""The Phase 1 generator: a fixed template of intro, songs, transitions and outro.

Two flows live here. A show of a set length is made whole by `generate`: every track is
picked first, then every script is written, then the audio is assembled. An endless show
(D042, D044) is made in steps: `generate` makes its opening only, the intro and the first
song, and each `extend` call adds one break and one song, or the closing. Both flows use
the same writer, the same shapes, the same facts lookup and the same audio treatment. A
step keeps almost nothing in memory: what it needs to go on is read back from the show.
"""

import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from radio_llama import trace
from radio_llama.audio.assemble import DEMO_SONG_LENGTH_S, assemble_show, write_parts, write_speech
from radio_llama.config import Settings
from radio_llama.facts import FactLookup
from radio_llama.generator.base import GenerationError, ProgressCallback
from radio_llama.llm.base import LLMClient, LLMResponse
from radio_llama.models import ENERGIES, Energy, Fact, Segment, Show, ShowConfig, Track
from radio_llama.music import (
    JamendoSource,
    MusicSource,
    SongProposal,
    WishFilters,
    YouTubeQuotaError,
    YouTubeSource,
    missing_source_message,
)
from radio_llama.personas import get_persona
from radio_llama.script.playlist import propose_playlist
from radio_llama.script.shapes import Shape, load_shapes, plan_shapes
from radio_llama.script.wish import map_wish
from radio_llama.script.writer import TRANSCRIPT_MEMORY, ScriptWriter
from radio_llama.trace import TRACE_DIR, script_trace_dir, tracing
from radio_llama.voice.base import TextToSpeech, Voice
from radio_llama.voice.factory import voice_for

# Facts kept per track once the looked-up ones are added. The host uses at most two.
MAX_TRACK_FACTS = 5

# An endless show (D044).
OPENING_LENGTH = 2  # the segments of the opening: the intro and the first song
# Songs the model proposes at a time for an endless YouTube show. Each one costs a search
# only when its turn comes, so a batch lasts eight steps. A longer reply is cut to this.
PLAYLIST_BATCH = 8
# An endless YouTube show has nothing left when this many playlists in a row bring no new
# song with a playable video. With the batch size, it bounds what a narrow wish can cost
# in one step: two playlist calls, and one search per proposal of two batches plus the
# proposals left from the step before.
MAX_DRY_PLAYLISTS = 2
# The API answers a 400 with the same sentence when the show is created.
ENDLESS_SOURCE_ERROR = "An endless show needs the 'jamendo' or 'youtube' music source."
# Why a show closed by itself, for `Show.end_note`. The player shows the sentence.
END_NOTE_NO_SONGS = "The show ended by itself: the music source had no more new songs for it."
END_NOTE_QUOTA = "The show ended by itself: YouTube's search quota for today is used up."
END_NOTE_CAP = "The show ended by itself: it reached the limit of {count} songs."


@dataclass
class _Upcoming:
    """What an endless show keeps in memory between two steps (D044).

    Everything else a step needs is read back from the show. These two only save an LLM
    call: after a restart they are gone and the next step asks again. The generator
    holds one of these, for the show that asked last, so it cannot grow with the server.
    """

    show_id: str
    filters: WishFilters | None = None  # Jamendo: the listener's wish, mapped once
    # YouTube: the songs the model proposed, in order. The first one may be the song of
    # the last step; the others were not looked up yet.
    proposals: list[SongProposal] = field(default_factory=list)


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
        # The little an endless show keeps between two steps (D044).
        self._upcoming: _Upcoming | None = None

    def generate(
        self, show: Show, out_dir: Path, on_progress: ProgressCallback | None = None
    ) -> Show:
        # Every API call of the run is recorded under the show folder (D037).
        with tracing(out_dir / TRACE_DIR):
            if show.config.endless:
                # Only the opening: the rest is made while the show plays (D044).
                with _recorded_step("opening", show):
                    return self._open(show, out_dir, on_progress)
            return self._generate(show, out_dir, on_progress)

    def extend(self, show: Show, out_dir: Path, *, end: bool = False) -> Show:
        """One more step of an endless show: a break and a song, or the closing (D044).

        Each step traces into the show's own trace folder, like the opening did (D037).
        """
        with tracing(out_dir / TRACE_DIR), _recorded_step("end" if end else "next", show):
            return self._extend(show, out_dir, end)

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
        # The voice the listener picked, else the persona's own (D040).
        voice = voice_for(writer.persona, self._settings, show.config.voice_id)

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
        music = self._music(config)

        # A seeded random sample of the candidates (D017), the listener's energy first
        # (D020); the same generator then picks the break shapes (D033).
        self.last_responses = []
        candidates = self._candidates(config, music)
        if len(candidates) < config.track_count:
            # A YouTube show has no style (D039): name the source instead.
            what = f"Style {config.style!r}" if config.style else f"Source {config.source!r}"
            raise GenerationError(
                f"{what} has {len(candidates)} tracks, {config.track_count} needed."
            )
        rng = random.Random(config.seed)
        tracks = pick_tracks(candidates, config.track_count, config.energy, rng)
        if on_progress is not None:
            on_progress(0, plan_length(len(tracks)))
        # Before the shapes: a track that gains facts can get the story break.
        tracks = self._with_facts(tracks, music, config.language)
        shapes = load_shapes(self._settings.prompts_dir, config.language)
        breaks = plan_shapes(shapes, [bool(track.facts) for track in tracks[1:]], rng)
        plan = _plan(tracks, breaks)
        return music, tracks, plan, {shape.id: shape for shape in shapes}, self._writer(config)

    def _music(self, config: ShowConfig) -> MusicSource:
        """The show's music source, or a readable error when this server has none by that name."""
        music = self._sources.get(config.source)
        if music is None:
            raise GenerationError(missing_source_message(config.source))
        return music

    def _with_facts(self, tracks: list[Track], music: MusicSource, language: str) -> list[Track]:
        """The tracks with their artists' looked-up facts, on a source that needs them (D036)."""
        lookup = self._facts
        if lookup is None or not music.needs_facts_lookup:
            return tracks
        return [_add_facts(track, lookup.artist_facts(track.artist, language)) for track in tracks]

    def _writer(self, config: ShowConfig) -> ScriptWriter:
        """The host: persona text for the prompts, and the clock read now (D031)."""
        persona = get_persona(self._settings.prompts_dir, config.language, config.persona)
        return ScriptWriter(
            self._llm,
            self._settings.prompts_dir,
            config.language,
            persona,
            config.style,
            energy=config.energy,
            wish=config.wish,
            now=datetime.now(self._settings.tzinfo),
        )

    def _candidates(self, config: ShowConfig, music: MusicSource) -> list[Track]:
        """The tracks to sample from, one search per source.

        Jamendo searches with the energy and the wish (D030). YouTube asks the model for
        songs, two more than needed, and keeps those with a playable video (D038). The
        catalog lists its tracks.
        """
        _check_wish(config, music)
        if isinstance(music, YouTubeSource):
            proposals = self._propose(config, config.track_count + 2)
            try:
                return music.find_tracks(proposals, minimum=config.track_count)
            except YouTubeQuotaError as exc:
                raise GenerationError(str(exc)) from exc
        # The catalog and Jamendo search by style; the API and the CLI check it is given.
        if config.style is None:
            raise GenerationError(f"Source {config.source!r} needs a style.")
        if isinstance(music, JamendoSource):
            filters = self._map_wish(config) if config.wish else None
            return music.find_tracks(
                config.style, energy=config.energy, filters=filters, minimum=config.track_count
            )
        return music.list_tracks(config.style)

    def _map_wish(self, config: ShowConfig) -> WishFilters:
        """One LLM call that turns the listener's wish into Jamendo search filters (D030).

        Only for a show that has a wish and a style: the callers check both.
        """
        filters, response = map_wish(
            self._llm,
            self._settings.prompts_dir,
            config.language,
            wish=config.wish,
            style=config.style,
            energy=config.energy,
        )
        self.last_responses.append(response)
        return filters

    def _propose(
        self, config: ShowConfig, count: int, played: list[Track] | None = None
    ) -> list[SongProposal]:
        """One LLM call that proposes `count` songs for a YouTube show (D038).

        No style on YouTube (D039): the wish is the theme, and the prompt says so.
        `played` is what an endless show already played (D044); a show of a set length
        passes nothing.
        """
        proposals, response = propose_playlist(
            self._llm,
            self._settings.prompts_dir,
            config.language,
            style=config.style,
            energy=config.energy,
            wish=config.wish,
            count=count,
            played=played,
        )
        self.last_responses.append(response)
        return proposals

    # The endless show (D042, D044): an opening, then one step at a time.

    def _open(self, show: Show, out_dir: Path, on_progress: ProgressCallback | None) -> Show:
        """The opening of an endless show: the intro and the first song.

        A failure here fails the show, like any show that cannot start: no first song,
        the YouTube quota used up, a script or a voice that cannot be made.
        """
        config = show.config
        music = self._music(config)
        if not music.endless:
            raise GenerationError(ENDLESS_SOURCE_ERROR)
        _check_wish(config, music)
        self.last_responses = []
        try:
            track = self._next_track(show, music, _step_rng(show))
        except YouTubeQuotaError as exc:
            raise GenerationError(str(exc)) from exc
        if track is None:
            # A YouTube show has no style (D039): name the source instead.
            what = f"Style {config.style!r}" if config.style else f"Source {config.source!r}"
            raise GenerationError(f"{what} has 0 tracks, 1 needed.")
        if on_progress is not None:
            on_progress(0, OPENING_LENGTH)
        track = self._with_facts([track], music, config.language)[0]
        # The song is fetched before any script is paid for, as for a show of a set length.
        song_audio = _song_audio(music, track)
        writer = self._writer(config)
        voice = voice_for(writer.persona, self._settings, config.voice_id)

        intro = Segment(kind="intro", text=writer.write_endless_intro(track))
        intro_audio = self._tts.synthesize(intro.text or "", voice)
        if on_progress is not None:
            on_progress(1, OPENING_LENGTH)
        self._append(show, out_dir, intro, intro_audio, track, song_audio)
        if on_progress is not None:
            on_progress(OPENING_LENGTH, OPENING_LENGTH)
        self.last_responses.extend(writer.responses)
        return show

    def _extend(self, show: Show, out_dir: Path, end: bool) -> Show:
        """Add a break and a song to an open show, or its closing.

        The closing is written when the listener asked for the end (`end`), or when the
        show cannot go on: `end_note` then says why. Any other failure (network, LLM,
        TTS, ffmpeg) fails this step only: nothing is saved and the step can be asked
        again.
        """
        config = show.config
        if not show.is_open or not show.tracks:
            raise GenerationError(f"Show {show.id} is not an endless show that is still open.")
        music = self._music(config)
        self.last_responses = []
        rng = _step_rng(show)
        track: Track | None = None
        end_note: str | None = None
        if not end:
            track, end_note = self._next_or_end_note(show, music, rng)

        # A writer is built at each step, so the clock is read at each step and the part
        # of the day can change during a long show (D044). What the host said so far is
        # read from the show, so a restart of the server keeps it.
        writer = self._writer(config)
        writer.transcript = [(s.kind, s.text or "") for s in show.segments if s.kind != "song"]
        voice = voice_for(writer.persona, self._settings, config.voice_id)

        if track is None:
            self._close(show, out_dir, writer, voice, end_note)
        else:
            self._add_part(show, out_dir, writer, voice, music, track, rng)
        self.last_responses.extend(writer.responses)
        return show

    def _close(
        self, show: Show, out_dir: Path, writer: ScriptWriter, voice: Voice, end_note: str | None
    ) -> None:
        """Write and voice the host's goodbye, and add it to the show.

        `end_note` is the sentence that says why the show ended by itself. It is None
        when the listener asked for the end.
        """
        # The outro gets the last songs only, like it gets the last scripts (D044).
        text = writer.write_outro(show.tracks[-TRANSCRIPT_MEMORY:])
        outro = Segment(kind="outro", text=text)
        self._append(show, out_dir, outro, self._tts.synthesize(text, voice))
        show.end_note = end_note

    def _add_part(
        self,
        show: Show,
        out_dir: Path,
        writer: ScriptWriter,
        voice: Voice,
        music: MusicSource,
        track: Track,
        rng: random.Random,
    ) -> None:
        """Write and voice the break that brings in `track`, and add both to the show."""
        track = self._with_facts([track], music, show.config.language)[0]
        # The song is fetched before the script is paid for, as in the opening.
        song_audio = _song_audio(music, track)
        # One shape, never the one of the break before (D033).
        shapes = load_shapes(self._settings.prompts_dir, show.config.language)
        shape = plan_shapes(shapes, [bool(track.facts)], rng, previous=_last_shape(show))[0]
        text = writer.write_transition(show.tracks[-1], track, shape)
        transition = Segment(kind="transition", track_id=track.id, shape=shape.id, text=text)
        speech_audio = self._tts.synthesize(text, voice)
        self._append(show, out_dir, transition, speech_audio, track, song_audio)

    def _next_or_end_note(
        self, show: Show, music: MusicSource, rng: random.Random
    ) -> tuple[Track | None, str | None]:
        """The next song, or None with the sentence that says why the show ends by itself."""
        cap = self._settings.endless_max_songs  # 0 means no limit
        if cap and len(show.tracks) >= cap:
            return None, END_NOTE_CAP.format(count=cap)
        try:
            track = self._next_track(show, music, rng)
        except YouTubeQuotaError:
            return None, END_NOTE_QUOTA
        if track is None:
            return None, END_NOTE_NO_SONGS
        return track, None

    def _next_track(self, show: Show, music: MusicSource, rng: random.Random) -> Track | None:
        """One more song the show has not played, or None when the source has none left.

        The guard against a repeat is the show itself: a track whose id is in
        `show.tracks` is never picked. That list is in show.json, so the guard holds
        after a restart too. The search differs per source, as in `_candidates`.
        """
        if self._upcoming is None or self._upcoming.show_id != show.id:
            self._upcoming = _Upcoming(show.id)  # another show asked last: start again
        if isinstance(music, YouTubeSource):
            return self._next_youtube_track(show, music, self._upcoming)
        if isinstance(music, JamendoSource):
            return self._next_jamendo_track(show, music, self._upcoming, rng)
        raise GenerationError(ENDLESS_SOURCE_ERROR)

    def _next_jamendo_track(
        self, show: Show, music: JamendoSource, upcoming: _Upcoming, rng: random.Random
    ) -> Track | None:
        """Search Jamendo past the played songs, then pick one like a show of a set length."""
        config = show.config
        if config.style is None:
            raise GenerationError(f"Source {config.source!r} needs a style.")
        # The wish is mapped to filters once per show (D030) and kept for the next steps.
        if config.wish and upcoming.filters is None:
            upcoming.filters = self._map_wish(config)
        fresh = music.more_tracks(
            config.style,
            energy=config.energy,
            filters=upcoming.filters,
            exclude={track.id for track in show.tracks},
        )
        if not fresh:
            return None
        return pick_tracks(fresh, 1, config.energy, rng)[0]

    def _next_youtube_track(
        self, show: Show, music: YouTubeSource, upcoming: _Upcoming
    ) -> Track | None:
        """The next song of an endless YouTube show, from the model's proposals.

        The proposals in memory come first. When none of them gives a new song, the model
        is asked for more, told what was played. After MAX_DRY_PLAYLISTS playlists in a
        row with nothing new, the show has nothing left.
        """
        track = self._first_new_video(show, music, upcoming)
        for _ in range(MAX_DRY_PLAYLISTS):
            if track is not None:
                break
            proposals = self._propose(show.config, PLAYLIST_BATCH, played=show.tracks)
            # The model can write more songs than it was asked for, and each one can cost
            # a search. The cut keeps a step bounded whatever the model does (D044).
            upcoming.proposals = proposals[:PLAYLIST_BATCH]
            track = self._first_new_video(show, music, upcoming)
        return track

    def _first_new_video(
        self, show: Show, music: YouTubeSource, upcoming: _Upcoming
    ) -> Track | None:
        """Walk the proposals in order until one has a playable video the show did not play.

        One search per proposal, and none for a song the show already played. A proposal
        that gives nothing is dropped. The one that gives the song stays first in the
        list. If the step fails after this, asking again finds the same song, and the
        source's 30 day cache answers with no new search (D044). Once the step has
        succeeded the song is in the show, so the next step drops the proposal here
        without a search.
        """
        played_ids = {track.id for track in show.tracks}
        played_songs = {_song_key(track.artist, track.title) for track in show.tracks}
        while upcoming.proposals:
            proposal = upcoming.proposals[0]
            # A song the show played, by its name: not worth a search.
            if _song_key(proposal.artist, proposal.title) not in played_songs:
                found = music.find_tracks([proposal], minimum=1)
                # Two proposals can lead to the same video: the id decides.
                if found and found[0].id not in played_ids:
                    return found[0]
            upcoming.proposals.pop(0)
        return None

    def _append(
        self,
        show: Show,
        out_dir: Path,
        speech: Segment,
        speech_audio: Path,
        track: Track | None = None,
        song_audio: Path | None = None,
    ) -> None:
        """Add a spoken segment and, with a track, its song to the show, and build their files.

        The last thing a step does. By now the script is written, the voice is made and
        the song is fetched, so only ffmpeg can still fail. Earlier segments and their
        files are left alone.
        """
        index = len(show.segments)
        sources = {index: speech_audio}
        show.segments.append(speech)
        if track is not None:
            song = Segment(kind="song", track_id=track.id)
            if song_audio is None:
                # An embedded video has no file: the segment says how long it plays (D038).
                song.duration_s = DEMO_SONG_LENGTH_S if show.config.demo else track.duration_s
            else:
                sources[index + 1] = song_audio
            show.segments.append(song)
            show.tracks.append(track)
        write_parts(show, sources, out_dir)
        # On an endless show this is not a target: it counts the songs so far (D044).
        show.config.track_count = len(show.tracks)


def _check_wish(config: ShowConfig, music: MusicSource) -> None:
    """Refuse a wish the source cannot use, and a source without styles that got no wish."""
    if config.wish and not music.accepts_wish:
        raise GenerationError("A wish needs the 'jamendo' or 'youtube' music source.")
    # A source without styles takes the wish as its theme (D039), so it needs one. The
    # API and the CLI check it too; a show built in code must not slip through.
    if not music.list_styles() and music.accepts_wish and not config.wish:
        raise GenerationError(f"Source {config.source!r} needs a wish: say what you want to hear.")


@contextmanager
def _recorded_step(kind: str, show: Show) -> Iterator[None]:
    """Write one line to `steps.jsonl` in the trace for a step of an endless show (D044).

    `kind` is what was asked: "opening", "next" or "end". The line holds how many
    segments the show had before and after, the song the step added, the `end_note`,
    the time the step took and its error. A failed step leaves no mark in show.json, so
    this line is its only record.
    """
    started = time.perf_counter()
    before = len(show.segments)
    try:
        yield
    except Exception as exc:
        trace.record(
            "steps",
            started,
            error=str(exc),
            kind=kind,
            segments_before=before,
            segments_after=before,  # nothing is kept of a failed step
            track_id=None,
            end_note=None,
        )
        raise
    last = show.segments[-1]
    trace.record(
        "steps",
        started,
        kind=kind,
        segments_before=before,
        segments_after=len(show.segments),
        track_id=last.track_id if last.kind == "song" else None,
        end_note=show.end_note,
    )


def _step_rng(show: Show) -> random.Random:
    """The random generator of one step of an endless show (D044).

    Seeded with the show's seed plus the number of songs so far: each step picks
    differently, a seeded show is reproducible, and nothing has to be carried from one
    step to the next. With no seed, a different pick every time.
    """
    if show.config.seed is None:
        return random.Random()
    return random.Random(show.config.seed + len(show.tracks))


def _last_shape(show: Show) -> str | None:
    """The shape of the show's last break, or None before the first one."""
    shapes = [segment.shape for segment in show.segments if segment.kind == "transition"]
    return shapes[-1] if shapes else None


def _song_audio(music: MusicSource, track: Track) -> Path | None:
    """The song's audio file, fetched now from a remote source. None for an embedded video."""
    if music.delivery == "embed":
        return None
    return music.audio_path(track)


def _song_key(artist: str, title: str) -> tuple[str, str]:
    """An artist and a title to compare two songs by, whatever the case and the spacing."""
    return " ".join(artist.casefold().split()), " ".join(title.casefold().split())


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
