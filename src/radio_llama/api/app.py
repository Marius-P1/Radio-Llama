"""Build the FastAPI application. Nothing here loads settings at import time."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import radio_llama
from radio_llama.api.jobs import Jobs, mark_stale_shows_failed
from radio_llama.api.routes import router
from radio_llama.config import Settings, load_settings
from radio_llama.generator import make_generator
from radio_llama.music import LocalCatalog

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    stale = mark_stale_shows_failed(settings.output_dir)
    if stale > 0:
        log.info("Marked %d unfinished show(s) as failed after restart", stale)
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Radio Llama API", version=radio_llama.__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.music = LocalCatalog(settings.catalog_path)
    app.state.jobs = Jobs(make_generator(settings), settings.output_dir)
    # Only the Vite dev server needs CORS; in production the backend serves the built
    # frontend (P1-12), so both share one origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app
