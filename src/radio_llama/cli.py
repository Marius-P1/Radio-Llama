"""The `radio-llama` command line: a developer tool for generating shows (P1-09)."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from pydantic import ValidationError

from radio_llama.api.app import create_app
from radio_llama.config import ConfigError, load_settings
from radio_llama.generator import make_generator, run_generation
from radio_llama.models import Show, ShowConfig
from radio_llama.storage import new_show_id

app = typer.Typer(
    help="Radio Llama: an LLM radio host between Creative Commons songs.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Radio Llama: an LLM radio host between Creative Commons songs."""
    # Typer turns a lone command into the root command; a callback keeps `radio-llama generate`.


@app.command()
def generate(
    style: Annotated[str, typer.Option(help="Music style from the catalog, for example jazz.")],
    persona: Annotated[str, typer.Option(help="Host persona id.")] = "calm",
    language: Annotated[
        str | None, typer.Option(help="en or fr. Defaults to RADIO_LANGUAGE.")
    ] = None,
    tracks: Annotated[int, typer.Option(help="Number of songs.")] = 3,
    song_length: Annotated[float, typer.Option(help="Seconds each song plays.")] = 30.0,
    seed: Annotated[int | None, typer.Option(help="Fix the random track pick.")] = None,
    fake: Annotated[
        bool, typer.Option("--fake", help="Use the fake LLM and fake TTS. No API keys needed.")
    ] = False,
    output: Annotated[
        Path | None, typer.Option(help="Folder for shows. Defaults to OUTPUT_DIR.")
    ] = None,
) -> None:
    """Generate one show into output/<show_id>/ as show.mp3 and show.json."""
    try:
        settings = load_settings()
        if fake:
            settings = settings.model_copy(update={"llm_provider": "fake", "tts_backend": "fake"})
        generator = make_generator(settings)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    try:
        config = ShowConfig(
            style=style,
            persona=persona,
            language=language or settings.radio_language,  # type: ignore[arg-type]
            track_count=tracks,
            song_length_s=song_length,
            seed=seed,
        )
    except ValidationError as exc:
        # One line per bad option, for example "language: Input should be 'en' or 'fr'".
        for error in exc.errors():
            typer.echo(f"{error['loc'][0]}: {error['msg']}", err=True)
        raise typer.Exit(code=1) from None

    now = datetime.now(UTC)
    show = Show(id=new_show_id(now), created_at=now, config=config)
    out_dir = (output or settings.output_dir) / show.id

    def on_progress(done: int, total: int) -> None:
        typer.echo(f"  {done}/{total} segments")

    typer.echo(f"Generating show {show.id}")
    show = run_generation(generator, show, out_dir, on_progress)

    if show.status != "ready":
        typer.echo(f"Generation failed: {show.error}", err=True)
        raise typer.Exit(code=1)
    tokens_in = sum(response.input_tokens for response in generator.last_responses)
    tokens_out = sum(response.output_tokens for response in generator.last_responses)
    typer.echo(f"Show written to {out_dir / show.audio_file}")
    typer.echo(f"Segments: {len(show.segments)}")
    typer.echo(f"LLM tokens: {tokens_in} in, {tokens_out} out")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Restart on code changes.")] = False,
) -> None:
    """Development server for the API."""
    uvicorn.run("radio_llama.api.app:create_app", factory=True, host=host, port=port, reload=reload)


@app.command("export-openapi")
def export_openapi(
    path: Annotated[Path, typer.Argument(help="Where to write the schema.")] = Path(
        "frontend/openapi.json"
    ),
) -> None:
    """Write the OpenAPI schema as JSON, for the frontend types (P1-11) and CI drift checks."""
    # The schema does not depend on the providers, so the fakes avoid needing any API key.
    settings = load_settings().model_copy(update={"llm_provider": "fake", "tts_backend": "fake"})
    schema = create_app(settings).openapi()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    typer.echo(f"OpenAPI schema written to {path}")


if __name__ == "__main__":
    sys.exit(app())
