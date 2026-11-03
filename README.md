# Radio Llama

Radio Llama is a radio show made by machines. An LLM writes a radio host's script between songs, a text-to-speech voice reads it, and the result is a short radio show as one MP3. The project is being rebuilt as a clean pipeline with a web app (Phase 1). A later phase turns the host into a tool-using agent with an evaluation harness (Phase 2).

Listen: [replay page](https://marius-p1.github.io/Radio-Llama/) (available after the v1.0 release). It plays pre-generated shows with no backend.

## Status

Phase 1 is in progress. The pipeline, the API and the web app run end to end with a fake LLM and a fake voice. No real show has been generated yet: every run so far used the fakes. The catalog holds 48 real Creative Commons tracks; their audio is published as a release asset. The replay page and the release are next. Tag `v0-hackathon` holds the original hackathon code.

## Quickstart

### Run it with Docker

You need Docker, a Mistral or Anthropic API key, and an ElevenLabs API key.

```
cp .env.example .env
```

Fill the keys in `.env`, then:

```
docker compose up --build
```

Open http://localhost:8000. Pick a style, an energy, a host and a language, press the button, and listen when the show is ready. The first start fetches the catalog audio into `catalog/audio/`. Shows are saved in `output/`, the voice cache in `cache/`.

<!-- TODO Marius: screenshot or short GIF of the web app -->
![The web app](docs-assets/web-app.png)

Audio sample: one minute of a real English show, attached to the release.
<!-- TODO Marius: link the release asset and list the track credits -->

### Try the pipeline without any key

Python 3.12, `uv` and ffmpeg on your path.

```
uv sync
uv run python catalog/download.py
uv run radio-llama generate --fake --style jazz --energy calm
```

You get `output/<show_id>/show.mp3` and `show.json`. The fake LLM returns a canned sentence and the fake voice is a faint hum, so the MP3 proves the pipeline, not the content. `--energy` is optional: `calm`, `medium` or `upbeat`, any energy when left out. `--seed` fixes the track pick.

Without the catalog audio, CI and the tests use the fixture catalog of test tones instead: `uv run python tests/fixtures/catalog/make_placeholder_audio.py`, then `CATALOG_PATH=tests/fixtures/catalog/catalog.json` in front of the command.

## How it works

1. Pick tracks. The pipeline samples a few tracks of the chosen style from the local catalog. When the listener asks for an energy, tracks with that energy come first, then tracks with no energy label, then the other energies, nearest first. A seed makes the pick reproducible.
2. Load the host. A persona (calm, energetic or witty) and the prompt files for the chosen language set the tone and the rules.
3. Write the script. One LLM call per spoken segment: an intro, a transition before each song after the first, an outro. Each call gets a word budget computed from a target length in seconds and a per-language speech rate. The intro receives the programme with each track's album, year and tags. Transitions receive the next track's album, year, tags, energy, its facts labelled artist or track, and the host notes from the catalog.
4. Voice the script. Each segment goes through text to speech behind a disk cache. The same text with the same voice is never synthesised twice.
5. Assemble the audio. ffmpeg trims each song and fades it in and out, normalises the loudness of every piece, then joins everything into one MP3.
6. Save and serve. `show.json` holds the script, the track list, the timings and the credits, next to `show.mp3`. The API serves both and the web app plays them.

### Layout

- `src/radio_llama/`: the Python package.
  - `config.py`: settings from the environment and `.env`.
  - `models.py`: `Track`, `Fact`, `Persona`, `ShowConfig`, `Segment`, `Show`.
  - `music/`: the `MusicSource` interface and the local catalog.
  - `llm/`: the `LLMClient` interface, the Mistral and Anthropic adapters, the fake.
  - `script/`: prompt loading, word budgets, the script writer.
  - `voice/`: the `TextToSpeech` interface, ElevenLabs, the disk cache, the fake.
  - `audio/`: ffmpeg calls and the show assembler.
  - `generator/`: the `ShowGenerator` interface, the pipeline, the run lifecycle.
  - `api/`: the FastAPI app, the `/api/v1` routes, the background jobs.
  - `cli.py`: the `radio-llama` command.
  - `storage.py`: reading and writing `show.json`.
- `prompts/en/` and `prompts/fr/`: system, intro, transition and outro prompts, plus the personas, one folder per language.
- `catalog/`: `catalog.json`, the download script and the checksums. See [catalog/README.md](catalog/README.md).
- `frontend/`: the React web app. See [frontend/README.md](frontend/README.md).
- `tests/`: pytest, fakes only. `tests/fixtures/catalog/` holds the placeholder catalog of test tones that CI plays.

## Design notes

- Music comes from a local catalog of Creative Commons tracks, not from Spotify. Spotify's terms forbid capturing audio and feeding its content to AI models, and the hackathon's recommendation endpoint is closed to new apps. A local catalog also gives the same inputs on every run, which the Phase 2 evaluation needs. Every fact in the catalog has a source URL and a quote.
- Radio Llama is a non-commercial demo. Any Creative Commons license that allows redistribution with attribution is accepted, including non-commercial (NC) ones; ND is excluded. A show is a collection of tracks, not an adaptation: each track keeps its own license, and every show credits each track with its artist, album, license and source links.
- The LLM is reached through my own thin interface, with Mistral and Anthropic adapters built on the official SDKs. No LiteLLM, no agent framework. I can explain every line, and Phase 2 compares providers and their tool-call formats, which a framework would hide.
- Prompts are files, one folder per language, English and French. The two languages declare the same placeholders and a test checks it. Quality is evaluated in English only.
- Text to speech sits behind a disk cache keyed by a hash of the text, the voice id, the model id and the voice settings. The filesystem is the index. Running the same show twice makes no second ElevenLabs call.
- Loudness normalisation is single-pass. A two-pass run doubles the runtime for a gain a demo does not need.
- The API is job-based: create a show, poll its status, fetch the audio. One show generates at a time. Shows are files in `output/`. No database, no task queue. One user at a time is the real use case.
- The create request has a `generator` field that only accepts `pipeline` today. Phase 2 adds `agent` next to it, so the pipeline stays as the baseline the agent is compared against.
- The web app is a separate React and Vite app. Its API types are generated from the backend's OpenAPI schema, so a breaking backend change fails the frontend build.
- The public demo is a static replay page on GitHub Pages, not a live deployment. It costs nothing, cannot be abused, and reuses the real frontend in a read-only mode.
- The Anthropic adapter accepts `temperature` but ignores it, because the Anthropic API no longer supports the parameter. Keeping it in the shared signature means every client is called the same way. Mistral still honours it.

## Development

Python 3.12 with `uv`. `uv sync` creates `.venv/`.

Run the checks:

```
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests never call a paid API. The Python side is linted and formatted with ruff, the frontend is linted with oxlint.

### Dev mode

Terminal 1, the API on port 8000 with reload:

```
uv run radio-llama serve --reload
```

Terminal 2, Vite on port 5173, which proxies `/api` to the backend:

```
cd frontend && npm install && npm run dev
```

After an API change, run `uv run radio-llama export-openapi` and then `npm run generate-types` in `frontend/`. CI fails if either generated file is stale.

## Limitations

- French generation runs end to end, but its quality is not evaluated. Only English is.
- The host knows only the facts written by hand in the catalog, and the prompt tells it to use nothing else. It does not search the web.
- Segment timing is estimated from a word count and a fixed speech rate, not measured from the speech.
- One show generates at a time. A second request waits.
- Everything so far was tested with fakes. No real LLM or ElevenLabs call has been made in this repo yet.
- The energy labels in the catalog are the curator's; all jazz tracks have none.
- French shows receive the host notes in English.
- The ElevenLabs voice ids in `prompts/*/personas.json` are placeholders.
- The Anthropic adapter ignores `temperature`.
- The web app has not been tested with a non-technical person yet.
- The web app has no deep links. Reloading the page returns to the first screen.

## Release checklist

For Marius, before tagging `v1.0`.

1. Publish the catalog audio: zip `catalog/audio/` without the `fake-*` files, attach it to a `catalog-v1` release, write its SHA-256 into `catalog/SHA256SUMS`. See [catalog/README.md](catalog/README.md).
2. Run `uv run python catalog/download.py` from a clean checkout, then `uv run pytest`.
3. Set the real ElevenLabs voice ids in `prompts/en/personas.json` and `prompts/fr/personas.json`.
4. Run one real show with Mistral or Anthropic and ElevenLabs, and listen to it.
5. Generate the replay shows and export them: `radio-llama export-replay <id>... --zip replay-shows.zip`. Attach the zip to the release.
6. Take the screenshot and cut the one minute audio sample. Link both above.
7. Tag.

## Origins

Radio Llama won first prize at SHIFT Nantes 2024, a 48 hour hackathon. The team of 5: Amine Saboni, Léo Joly-Jehenne, Romain Jolidon, Tak Castel, Marius Pain. The original repo is at https://github.com/SaboniAmine/radio_llama. Marius's part was the script and voice generation (LLM prompts and ElevenLabs).

The hackathon tree is preserved at tag `v0-hackathon`. The copyrighted music files, caches and a hardcoded database URL were removed. Per-commit history stays in the original repo.

`frontend/assets/img/bg.jpg` in that snapshot comes from the hackathon's UI theme. Its original source is not recorded.

## Roadmap

- Phase 1 (this): the template pipeline, the API, the web app, the replay page.
- Phase 2: a tool-using agent as a second generator, an evaluation harness, a pipeline versus agent comparison, and an OpenAI-compatible adapter for local models.

## License

MIT, see [LICENSE](LICENSE). Hackathon code copyright the team, later work copyright Marius Pain.
