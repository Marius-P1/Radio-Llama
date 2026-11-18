"""The `/api/v1` routes: options, voices, create a show, read shows, fetch the audio, and
the two steps of an endless show (D044): its next part and its end."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from radio_llama.api.jobs import Jobs, StepRefused
from radio_llama.api.schemas import (
    CreateShowRequest,
    EndShowRequest,
    NextPartRequest,
    OptionsResponse,
    PersonaOption,
    ShowResponse,
    ShowSummary,
    SourceOption,
    has_own_audio,
)
from radio_llama.config import Settings
from radio_llama.generator.lifecycle import EXPECTED_ERRORS
from radio_llama.generator.pipeline import ENDLESS_SOURCE_ERROR
from radio_llama.models import ENERGIES, Show, ShowConfig
from radio_llama.music import SOURCE_NAMES, MusicSource, missing_source_message, playback_of
from radio_llama.personas import load_personas
from radio_llama.storage import SHOW_FILE, load_show, new_show_id, save_show
from radio_llama.voice import TTSError, UnknownVoiceError, VoiceList, VoiceOption, find_voice

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

LANGUAGES = ["en", "fr"]
SHOW_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")  # see new_show_id


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_sources(request: Request) -> dict[str, MusicSource]:
    return request.app.state.sources


def get_jobs(request: Request) -> Jobs:
    return request.app.state.jobs


def get_voice_list(request: Request) -> VoiceList:
    return request.app.state.voices


SettingsDep = Annotated[Settings, Depends(get_settings)]
SourcesDep = Annotated[dict[str, MusicSource], Depends(get_sources)]
JobsDep = Annotated[Jobs, Depends(get_jobs)]
VoicesDep = Annotated[VoiceList, Depends(get_voice_list)]


def show_dir_or_404(output_dir: Path, show_id: str) -> Path:
    """The folder of the show `show_id`, or a 404 when there is no such show."""
    not_found = HTTPException(status_code=404, detail=f"Show {show_id!r} not found.")
    # The id comes from the URL: rejecting anything that is not a show id prevents path traversal.
    if not SHOW_ID.match(show_id):
        raise not_found
    show_dir = output_dir / show_id
    if not (show_dir / SHOW_FILE).is_file():
        raise not_found
    return show_dir


def load_show_or_404(output_dir: Path, show_id: str) -> Show:
    """Read `output_dir / show_id / show.json`, or raise a 404."""
    return load_show(show_dir_or_404(output_dir, show_id))


def _summary(show: Show) -> ShowSummary:
    return ShowSummary(
        id=show.id,
        status=show.status,
        created_at=show.created_at,
        config=show.config,
        error=show.error,
    )


@router.get("/options")
def get_options(settings: SettingsDep, sources: SourcesDep) -> OptionsResponse:
    personas = {
        language: [
            PersonaOption(
                id=persona.id,
                name=persona.name,
                description=persona.description,
                voice_id=persona.voice_id,
            )
            for persona in load_personas(settings.prompts_dir, language)
        ]
        for language in LANGUAGES
    }
    return OptionsResponse(
        sources=[
            SourceOption(
                id=name,  # type: ignore[arg-type]
                name=SOURCE_NAMES[name],
                styles=music.list_styles(),
                wish=music.accepts_wish,
                endless=music.endless,
            )
            for name, music in sources.items()
        ],
        personas=personas,
        languages=LANGUAGES,
        energies=list(ENERGIES),
    )


@router.get("/voices")
def list_voices(voices: VoicesDep) -> list[VoiceOption]:
    """The voices the host can speak with (D040).

    Its own route and not a part of `/options`: with ElevenLabs it is a remote call, and
    a slow or failing one must not hold back the rest of the create screen.
    """
    try:
        return voices.list_voices()
    except TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/shows", status_code=202)
def create_show(
    body: CreateShowRequest,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    sources: SourcesDep,
    jobs: JobsDep,
    voices: VoicesDep,
) -> ShowSummary:
    music = sources.get(body.source)
    if music is None:
        raise HTTPException(status_code=400, detail=missing_source_message(body.source))
    # A source with styles needs one of them. A source without styles (YouTube) takes none:
    # the wish is its theme, so it needs a wish instead (D039).
    styles = music.list_styles()
    if not styles:
        if body.style is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Source {body.source!r} has no styles to pick from. Leave the style out "
                    "and say what you want to hear in the wish."
                ),
            )
    elif body.style not in styles:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown style {body.style!r} for source {body.source!r}. "
                f"Known styles: {', '.join(styles)}."
            ),
        )
    # A blank wish is no wish. A wish only means something on a source that takes one (D030).
    wish = (body.wish or "").strip() or None
    if wish is not None and not music.accepts_wish:
        raise HTTPException(
            status_code=400, detail="A wish needs the 'jamendo' or 'youtube' music source."
        )
    if wish is None and not styles and music.accepts_wish:
        raise HTTPException(
            status_code=400,
            detail=f"A {SOURCE_NAMES[body.source]} show needs a wish: say what you want to hear.",
        )
    # A show with no set length needs a source that can always give one more song (D042).
    if body.endless and not music.endless:
        raise HTTPException(status_code=400, detail=ENDLESS_SOURCE_ERROR)
    persona_ids = [persona.id for persona in load_personas(settings.prompts_dir, body.language)]
    if body.persona not in persona_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown persona {body.persona!r} for language {body.language!r}. "
                f"Known personas: {', '.join(persona_ids)}."
            ),
        )
    # A picked voice must be one of the listed ones. Its name is stored with the show, so
    # the player can show it later without the voice list (D040). A blank voice is no
    # voice, like a blank wish: the list is then not read.
    try:
        voice = find_voice(voices, body.voice_id)
    except UnknownVoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    now = datetime.now(UTC)
    show = Show(
        id=new_show_id(now),
        created_at=now,
        config=ShowConfig(
            style=body.style,
            persona=body.persona,
            voice_id=voice.id if voice is not None else None,
            voice_name=voice.name if voice is not None else None,
            language=body.language,
            generator=body.generator,
            energy=body.energy,
            source=body.source,
            wish=wish,
            # An endless show counts the songs it has so far: none yet (D044).
            track_count=0 if body.endless else body.track_count,
            demo=body.demo,
            endless=body.endless,
        ),
    )
    save_show(show, settings.output_dir / show.id)
    background_tasks.add_task(jobs.run, show)
    return _summary(show)


@router.get("/shows")
def list_shows(settings: SettingsDep) -> list[ShowSummary]:
    shows: list[Show] = []
    if settings.output_dir.is_dir():
        for show_dir in settings.output_dir.iterdir():
            if not (show_dir / SHOW_FILE).is_file():
                continue
            try:
                shows.append(load_show(show_dir))
            except ValueError:
                log.warning("Skipping unreadable %s", show_dir / SHOW_FILE)
    shows.sort(key=lambda show: show.created_at, reverse=True)
    return [_summary(show) for show in shows]


@router.get("/shows/{show_id}")
def get_show(show_id: str, settings: SettingsDep, jobs: JobsDep) -> ShowResponse:
    show = load_show_or_404(settings.output_dir, show_id)
    return ShowResponse.from_show(show, jobs.progress(show.id))


@router.get("/shows/{show_id}/audio")
def get_show_audio(show_id: str, settings: SettingsDep) -> FileResponse:
    show = load_show_or_404(settings.output_dir, show_id)
    path = settings.output_dir / show_id / (show.audio_file or "")
    if show.status != "ready" or show.audio_file is None or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Show {show_id!r} has no audio yet.")
    # No filename: the browser plays the MP3 inline instead of downloading it.
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/shows/{show_id}/segments/{index}/audio")
def get_segment_audio(show_id: str, index: int, settings: SettingsDep) -> FileResponse:
    """One segment's audio file of a ready show, for per-segment playback (D038, D044)."""
    # Read without the generation lock: with it, the audio of a playing show would wait
    # behind a step that takes tens of seconds. The price: a request that arrives at the
    # instant a step saves show.json can read a half-written file and fail.
    show = load_show_or_404(settings.output_dir, show_id)
    not_found = HTTPException(
        status_code=404, detail=f"Segment {index} of show {show_id!r} has no audio."
    )
    if show.status != "ready" or not 0 <= index < len(show.segments):
        raise not_found
    segment = show.segments[index]
    # The host's speech files, and the songs of a show that plays by segments from files
    # (an endless Jamendo show, D044). A song of a file show is never served here: it is
    # inside show.mp3. A YouTube song has no file at all.
    if segment.audio_file is None or not has_own_audio(segment, playback_of(show.config)):
        raise not_found
    path = settings.output_dir / show_id / segment.audio_file
    if not path.is_file():
        raise not_found
    # One URL can name two files over the life of an endless show: after an `end`, the
    # index of the dropped break is the index of the goodbye (D044). `no-cache` makes the
    # browser ask the server again before it reuses a copy. FileResponse never answers
    # "304 Not Modified", so the file is then sent again: a segment played twice is
    # downloaded twice.
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})


@router.post("/shows/{show_id}/next")
def next_part(
    show_id: str, body: NextPartRequest, settings: SettingsDep, jobs: JobsDep
) -> ShowResponse:
    """Add the next break and song to an endless show, and return the whole show (D044).

    The player asks at the middle of the current song. The request blocks until the part
    is made, about as long as one song of a show takes to generate. The reply is the
    show with two more segments, or with the host's goodbye when the show had to end by
    itself (`end_note` says why). Asking again with the same `known` makes nothing new.

    404: no such show. 409: the show cannot take a step (not endless, an opening that
    failed or has not started, `known` larger than the show). 502: the step failed for a
    reason the pipeline expects (a provider, the network, ffmpeg). show.json was not
    touched, so the same request can be sent again.

    A request that arrives while the opening is being made is not refused. It waits for
    the opening, as every step waits for what is being generated (D011), and is then
    answered from the show as it is: the next part when the opening succeeded, a 409
    when it failed.
    """
    # Only that the show exists: show.json is read once, by `Jobs`, under the lock, so
    # a request that arrives while another one saves the show waits for it.
    show_dir_or_404(settings.output_dir, show_id)
    try:
        show = jobs.extend(show_id, body.known)
    except StepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EXPECTED_ERRORS as exc:
        log.exception("The next part of show %s failed", show_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ShowResponse.from_show(show, None)


@router.post("/shows/{show_id}/end")
def end_show(
    show_id: str, body: EndShowRequest, settings: SettingsDep, jobs: JobsDep
) -> ShowResponse:
    """Close an endless show after the song at segment `after`, and return the show (D044).

    The part made past that song, one break and one song at most, is dropped and the
    host's goodbye takes its place. A show that is already closed is returned as it is.

    The errors are those of `next_part`. The 409 also covers an `after` that is not a
    song of the show, and an `after` with more than one song behind it: the player is
    never more than one part ahead, and this request never deletes more than that.
    """
    show_dir_or_404(settings.output_dir, show_id)  # as in `next_part`
    try:
        show = jobs.end(show_id, body.after)
    except StepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EXPECTED_ERRORS as exc:
        log.exception("The closing of show %s failed", show_id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return ShowResponse.from_show(show, None)
