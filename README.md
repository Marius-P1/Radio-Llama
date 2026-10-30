# Radio Llama

Radio Llama is a radio show made by machines. An LLM writes a radio host's script between songs, a text-to-speech voice reads it, and the result is a short radio show as one MP3. The project is being rebuilt as a clean pipeline with a web app (Phase 1). A later phase turns the host into a tool-using agent with an evaluation harness (Phase 2).

## Status

Phase 1 in progress. The pipeline, the API and the web app run end to end with fake LLM and TTS. Replay page and release are next. Tag `v0-hackathon` holds the original hackathon code.

## Origins

Radio Llama won first prize at SHIFT Nantes 2024, a 48 hour hackathon. The team of 5: Amine Saboni, Léo Joly-Jehenne, Romain Jolidon, Tak Castel, Marius Pain. The original repo is at https://github.com/SaboniAmine/radio_llama. Marius's part was the script and voice generation (LLM prompts and ElevenLabs).

The hackathon tree is preserved at tag `v0-hackathon`. The copyrighted music files, caches and a hardcoded database URL were removed. Per-commit history stays in the original repo.

`frontend/assets/img/bg.jpg` in that snapshot comes from the hackathon's UI theme. Its original source is not recorded.

## What the rebuild adds after v0-hackathon

- Local Creative Commons catalog with sourced facts (planned)
- English and French (planned)
- One thin LLM interface with Mistral and Anthropic adapters on official SDKs (planned)
- TTS disk cache (planned)
- ffmpeg assembly with fades and loudness normalisation (planned)
- FastAPI job API plus React web app (planned)
- Static replay page on GitHub Pages (planned)
- Evaluation harness and tool-using agent in Phase 2 (planned)

## Run it

You need Docker, a Mistral or Anthropic API key, and an ElevenLabs API key.

```
cp .env.example .env
```

Fill the keys in `.env`, then:

```
docker compose up --build
```

Open http://localhost:8000.

The first start generates placeholder audio (or downloads the real catalog once it exists) into `catalog/audio/`. Shows are saved in `output/`, the voice cache in `cache/`.

## Development

Python 3.12 with `uv`. `uv sync` creates `.venv/`.

Run the checks:

```
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Tests never call a paid API.

### Generate a show with fakes

```
uv sync
uv run python catalog/download.py
uv run radio-llama generate --fake --style jazz
```

The fake LLM returns a canned sentence and the fake voice is a faint hum, so the MP3 proves the pipeline, not the content.

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

## License

MIT, see [LICENSE](LICENSE). Hackathon code copyright the team, later work copyright Marius Pain.
