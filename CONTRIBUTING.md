# Contributing to Radio Llama

Thanks for your interest. Radio Llama is a small personal project, so the process is light. This file is also the brief for coding agents: if you work with one, point it here.

## How to contribute

1. For anything bigger than a small fix, open an issue first. It saves you work if the change does not fit the project.
2. Fork the repo and create a branch in your fork.
3. Make your change, run the checks below, and open a pull request against `main`.
4. CI must pass before a review. The maintainer merges.

Only the maintainer pushes to this repo. Every change arrives as a pull request from a fork.

## Setup

You need Python 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg on your path, and Node 22 for the frontend.

```
uv sync
cd frontend && npm ci
```

`uv sync` creates `.venv/`. Run every Python tool through `uv run`.

The real catalog audio is a release asset, about 350 MB:

```
uv run python catalog/download.py
```

You can skip it. The fixture catalog of test tones is enough for development:

```
uv run python tests/fixtures/catalog/make_placeholder_audio.py
CATALOG_PATH=tests/fixtures/catalog/catalog.json uv run radio-llama generate --fake --style jazz --demo
```

See the README for dev mode (the API with reload and the Vite dev server).

## Fakes only

Tests and CI never call a paid API. Use the fakes:

- `radio-llama generate --fake` runs with the fake LLM and the fake voice.
- `LLM_PROVIDER=fake` and `TTS_BACKEND=fake` do the same for the server.
- New code that talks to a provider gets a fake or a recorded response in `tests/fixtures/`, never a live call.

You only need real API keys to hear a real show, and that is never required for a pull request.

## Checks

Run these before you open a pull request. CI runs the same ones.

```
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

For frontend changes, also run in `frontend/`:

```
npm run lint
npm run build
npm run build:replay
```

`npm run build` also type checks.

After a change to an API model or route, regenerate the two generated files and commit them with the change. CI fails when they are stale.

```
uv run radio-llama export-openapi frontend/openapi.json
cd frontend && npm run generate-types
```

## Code

- Simple, readable code over clever code. No abstraction without two real uses.
- Python 3.12, `ruff`, `pytest`, `typer` for the CLI, FastAPI, `pydantic-settings`.
- Official provider SDKs only: `mistralai`, `anthropic`, `elevenlabs`. ffmpeg is called through `subprocess`.
- Prompts live in `prompts/en/` and `prompts/fr/`, never in Python strings. Both languages declare the same placeholders; a test checks it.
- Package code lives in `src/radio_llama/`, tests in `tests/`.
- Frontend: React, Vite, TypeScript, plain CSS, no UI library. Assets in `frontend/public/` stay small.
- Few dependencies. Ask in an issue before adding one.

To add a track to the catalog, follow [catalog/README.md](catalog/README.md).

## Never commit

Secrets, `.env`, audio files, `output/`, `cache/`, `catalog/audio/`, `node_modules/` or build artifacts. The `.gitignore` covers them. Keep it that way.

## Commits

Small commits, one logical change each. The subject line:

- starts with a [gitmoji](https://gitmoji.dev/), for example `✨` for a feature, `🐛` for a bug fix, `📝` for docs, `✅` for tests, `♻️` for a refactor;
- is imperative and capitalized, 50 characters or less, with no final period.

The body, after a blank line, explains why the change is needed. Wrap it at 72 characters.

```
🐛 Report a bad --language value in one line

Typer printed a stack trace when the language was not "en" or "fr".
The error now names the option and the accepted values.
```

## Writing style

For the README and other docs: plain English, short sentences, no em dashes, no marketing language.

## License

By contributing, you agree that your contribution is released under the [MIT License](LICENSE).
