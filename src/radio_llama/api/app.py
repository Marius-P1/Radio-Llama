"""Build the FastAPI application. Nothing here loads settings at import time."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import radio_llama
from radio_llama.api.jobs import Jobs, mark_stale_shows_failed
from radio_llama.api.routes import router
from radio_llama.config import Settings, load_settings
from radio_llama.generator import make_generator
from radio_llama.music import MusicSource, make_music_sources

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    stale = mark_stale_shows_failed(settings.output_dir)
    if stale > 0:
        log.info("Marked %d unfinished show(s) as failed after restart", stale)
    yield


def create_app(
    settings: Settings | None = None, sources: dict[str, MusicSource] | None = None
) -> FastAPI:
    """`sources` is for tests that inject a music source with a fake transport (D030)."""
    settings = settings or load_settings()
    if sources is None:
        sources = make_music_sources(settings)
    app = FastAPI(title="Radio Llama API", version=radio_llama.__version__, lifespan=lifespan)
    app.state.settings = settings
    # The routes list and validate against the same sources the generator searches.
    app.state.sources = sources
    app.state.jobs = Jobs(make_generator(settings, sources), settings.output_dir)
    # Only the Vite dev server needs CORS; in production the backend serves the built
    # frontend (P1-12), so both share one origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    if settings.frontend_dist.is_dir():
        # The API routes are registered first so they win over the static mount; `html=True`
        # serves `index.html` at `/`. The app has no client-side router, so no deep links.
        app.mount("/", StaticFiles(directory=settings.frontend_dist, html=True), name="frontend")
        log.info("Serving the built frontend from %s", settings.frontend_dist)
    else:
        log.info("No built frontend at %s, serving the API only", settings.frontend_dist)
    return app
