"""The `/api/v1` routes: options, create a show, read shows, fetch the MP3."""

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
)
from radio_llama.config import Settings
from radio_llama.models import ENERGIES, Show, ShowConfig
from radio_llama.music import LocalCatalog
from radio_llama.personas import load_personas
from radio_llama.storage import SHOW_FILE, load_show, new_show_id, save_show

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

LANGUAGES = ["en", "fr"]
SHOW_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")  # see new_show_id


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_music(request: Request) -> LocalCatalog:
    return request.app.state.music


def get_jobs(request: Request) -> Jobs:
    return request.app.state.jobs


SettingsDep = Annotated[Settings, Depends(get_settings)]
MusicDep = Annotated[LocalCatalog, Depends(get_music)]
JobsDep = Annotated[Jobs, Depends(get_jobs)]


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
def get_options(settings: SettingsDep, music: MusicDep) -> OptionsResponse:
    personas = {
        language: [
            PersonaOption(id=persona.id, name=persona.name, description=persona.description)
            for persona in load_personas(settings.prompts_dir, language)
        ]
        for language in LANGUAGES
    }
    return OptionsResponse(
        styles=music.list_styles(),
        personas=personas,
        languages=LANGUAGES,
        energies=list(ENERGIES),
    )


@router.post("/shows", status_code=202)
def create_show(
    body: CreateShowRequest,
    background_tasks: BackgroundTasks,
    settings: SettingsDep,
    music: MusicDep,
    jobs: JobsDep,
) -> ShowSummary:
    styles = music.list_styles()
    if body.style not in styles:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown style {body.style!r}. Known styles: {', '.join(styles)}.",
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
    now = datetime.now(UTC)
    show = Show(
        id=new_show_id(now),
        created_at=now,
        config=ShowConfig(
            style=body.style,
            persona=body.persona,
            language=body.language,
            generator=body.generator,
            energy=body.energy,
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
