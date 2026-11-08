# Radio Llama frontend

The web page for Radio Llama. You pick a music source, a style, a host and a language, add a wish for Jamendo if you like, press one button, follow the progress, then listen to the show with its script and track credits. Past shows can be replayed.

React, Vite and TypeScript. The API types in `src/api/schema.d.ts` are generated from `openapi.json`, which the backend exports.

The look comes from the hackathon frontend. `public/` holds the background (`bg.jpg`, a web-sized copy of the hackathon image), the logo and the Azeret Mono font in `fonts/`, under the SIL Open Font License (`fonts/OFL.txt`). The original source of the background image is not recorded.

## Develop

```sh
npm install
npm run dev
```

The dev server runs on port 5173 and proxies `/api` to the backend on port 8000. Start the backend from the repository root first:

```sh
uv run radio-llama serve --reload
```

## After a backend change

Export the schema and regenerate the types. CI fails if either file is out of date.

```sh
uv run radio-llama export-openapi frontend/openapi.json
cd frontend && npm run generate-types
```

## Check and build

```sh
npm run lint
npm run build
```

The build goes to `frontend/dist/`, which the backend serves in production.

## Replay mode

The replay page is the same app built read-only. It plays shows exported as static files, so it needs no backend and runs on GitHub Pages. The create screen is hidden and the shows are listed from `public/shows/index.json`. Each show credits its tracks with their own license, and the footer credits ElevenLabs for the voice.

```sh
npm run build:replay
```

This sets `VITE_REPLAY=1` through `.env.replay` and builds with the base path `/Radio-Llama/`, the path of a GitHub Pages project site.

The shows come from the backend's output folder. From the repository root:

```sh
uv run radio-llama export-replay <show_id>... --zip replay-shows.zip
```

This copies each ready show's `show.json` and `show.mp3` into `frontend/public/shows/<id>/`, rewrites `index.json`, and packs the folder into `replay-shows.zip`. That folder and the zip are git-ignored: audio is never committed.

`.github/workflows/pages.yml` deploys the replay build when a `v*` tag is pushed. It downloads `replay-shows.zip` from the release of that tag, unpacks it into `public/shows/`, runs `npm run build:replay` and publishes `dist/` to Pages. The release must carry the zip, and Pages must be enabled with GitHub Actions as the source.
