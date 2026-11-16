# Radio Llama frontend

The web page for Radio Llama. You pick a music source, a style, a host style, a voice and a language, add a wish for Jamendo if you like, press one button, follow the progress, then listen to the show with its script and track credits. On YouTube there is no style to pick: you say what you want to hear, and that wish is the only required field. Past shows can be replayed.

The voice list (`src/VoicePicker.tsx`) shows the voices the backend returns on `/api/v1/voices`: the voices of the ElevenLabs account, or five made-up ones with the fake voice. You can search it, filter it by gender, and play a sample of a voice before picking it. The browser loads the samples from ElevenLabs. Voices verified for the show's language come first. Until you pick a voice, the show uses the one that comes with the host style. If the list cannot be loaded, the form still works and says so.

A catalog or Jamendo show plays as one MP3 in the browser's audio player. A YouTube show has no MP3: the page plays the host's segments from their own files and each song in the embedded YouTube player, in script order, with Play, Pause and Next (`src/SegmentPlayer.tsx`). The YouTube player is created once when the show opens and stays visible while the show plays, as YouTube's policies ask. The app itself stores nothing about who listens.

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

The replay page is the same app built read-only. It plays shows exported as static files, so it needs no backend and runs on GitHub Pages. The create screen is hidden and the shows are listed from `public/shows/index.json`. A catalog or Jamendo show plays as one MP3. A YouTube show plays segment by segment: the host's speech files are static files too, and the songs play in the YouTube player, so the visitor's browser talks to YouTube. Each show credits its tracks with their own license, and the footer credits ElevenLabs for the voice.

```sh
npm run build:replay
```

This sets `VITE_REPLAY=1` through `.env.replay` and builds with the base path `/Radio-Llama/`, the path of a GitHub Pages project site.

The shows come from the backend's output folder. From the repository root:

```sh
uv run radio-llama export-replay <show_id>... --zip replay-shows.zip
```

This copies each ready show's `show.json` and its audio into `frontend/public/shows/<id>/`, rewrites `index.json`, and packs the folder into `replay-shows.zip`. The audio is `show.mp3` for a catalog or Jamendo show and one speech file per host segment (`00-intro.mp3`, `02-transition.mp3`, ...) for a YouTube show, which has no MP3. That folder and the zip are git-ignored: audio is never committed.

`.github/workflows/pages.yml` deploys the replay build when a `v*` tag is pushed. It downloads `replay-shows.zip` from the release of that tag, unpacks it into `public/shows/`, runs `npm run build:replay` and publishes `dist/` to Pages. The release must carry the zip, and Pages must be enabled with GitHub Actions as the source. A new set of shows, YouTube shows included, needs a new `v*` release with its own `replay-shows.zip`.
