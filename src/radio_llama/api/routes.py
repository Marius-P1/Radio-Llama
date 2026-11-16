"""The `/api/v1` routes: options, voices, create a show, read shows, fetch the audio."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from radio_llama.api.jobs import Jobs
from radio_llama.api.schemas import (
    CreateShowRequest,
    OptionsResponse,
    PersonaOption,
    ShowResponse,
    ShowSummary,
    SourceOption,
)
from radio_llama.config import Settings
from radio_llama.models import ENERGIES, Show, ShowConfig
from radio_llama.music import SOURCE_NAMES, MusicSource, missing_source_message
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


def load_show_or_404(output_dir: Path, show_id: str) -> Show:
    """Read `output_dir / show_id / show.json`, or raise a 404."""
    not_found = HTTPException(status_code=404, detail=f"Show {show_id!r} not found.")
    # The id comes from the URL: rejecting anything that is not a show id prevents path traversal.
    if not SHOW_ID.match(show_id):
        raise not_found
    show_dir = output_dir / show_id
    if not (show_dir / SHOW_FILE).is_file():
        raise not_found
    return load_show(show_dir)


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
            demo=body.demo,
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
    """One speech file of a ready show, for per-segment playback (D038)."""
    show = load_show_or_404(settings.output_dir, show_id)
    not_found = HTTPException(
        status_code=404, detail=f"Segment {index} of show {show_id!r} has no audio."
    )
    if show.status != "ready" or not 0 <= index < len(show.segments):
        raise not_found
    segment = show.segments[index]
    # A song is never served here: a file show keeps its songs inside show.mp3, an embed
    # show plays them in the YouTube player. Only the host's speech files are.
    if segment.kind == "song" or segment.audio_file is None:
        raise not_found
    path = settings.output_dir / show_id / segment.audio_file
    if not path.is_file():
        raise not_found
    return FileResponse(path, media_type="audio/mpeg")
