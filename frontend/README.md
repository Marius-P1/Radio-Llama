# Radio Llama frontend

The web page for Radio Llama. You pick a music source, a style, a host style, a voice, a language and a show length, add a wish for Jamendo if you like, press one button, follow the progress, then listen to the show with its script and track credits. On YouTube there is no style to pick: you say what you want to hear, and that wish is the only required field. Past shows can be replayed.

The voice list (`src/VoicePicker.tsx`) shows the voices the backend returns on `/api/v1/voices`: the voices of the ElevenLabs account, or five made-up ones with the fake voice. You can search it, filter it by gender, and play a sample of a voice before picking it. The browser loads the samples from ElevenLabs. The voices made for the show's language come first: those ElevenLabs lists for that language and those whose own language it is. Until you pick a voice, the show uses the one that comes with the host style. If the list cannot be loaded, the form still works and says so.

A catalog or Jamendo show of a set length plays as one MP3 in the browser's audio player. A YouTube show and every endless show have no MP3: the page plays them segment by segment, in script order, with Play, Pause and Next (`src/SegmentPlayer.tsx`). A segment that has a file plays in one audio element: the host's segments, and the songs of an endless Jamendo show. A song without a file is a YouTube video and plays in the embedded YouTube player. That player and its box exist only for a show that has a video. It is created once when the show opens and stays visible while the show plays, as YouTube's policies ask. The app itself stores nothing about who listens.

## The endless show

On a source that allows it (Jamendo, YouTube), the Show length block has a fifth button, Endless. The create request then carries `endless: true`, and the progress screen only waits for the opening: the host's first words and the first song.

The player that opens from the progress screen is live (`App.tsx` carries the flag in its view). Only a live player makes an endless show grow. A show opened from Past shows is a replay: it sends no request and has no End button. So a reload or a closed tab ends the live show.

What the live player does, all in `src/SegmentPlayer.tsx`:

- Once a second it reads the position of the song. When the last song it knows passes its middle, it sends `POST /api/v1/shows/{id}/next` once. In demo mode it sends it as soon as the song starts. A paused player sends nothing.
- The reply is the whole show with its new part. `Player.tsx` owns the show, so the title, the script and the credits grow. The audio element and the YouTube player are created once per show id, so a reply never interrupts the song.
- If the song ends before the reply, the player waits with "The host is getting the next song ready..." and goes on when the reply arrives. Next on the last song asks at once and waits the same way.
- If the request fails, the song keeps playing and the message is shown. The player sends the request once more when the song ends. After that only the Try again button sends it, because each request can cost an LLM call.
- End after this song sends `POST /api/v1/shows/{id}/end` with the index of the song that is playing, or of the next song when the host is speaking. Until the reply is in, the player starts nothing past that song. The reply holds the host's goodbye, which plays after the song. If the request fails, the button comes back with the message.
- Still listening? is the check that someone is there. A click is any pointer press or key press on the page, or a pause or play made in the YouTube player's own controls. When the player is about to ask for the next part and the last click is more than an hour old, it shows the button instead. The song that is playing finishes, then the show is silent until the button is pressed.
- When the host closes the show by itself, the reply ends with the goodbye and carries an `end_note`. The note is shown under the player, live and on replay. A show the listener left has no goodbye: its replay ends with "The show stopped here."

To test the check without waiting an hour, set `VITE_CHECK_IN_MINUTES` when building or starting the dev server. It takes a number of minutes, decimals allowed:

```sh
VITE_CHECK_IN_MINUTES=0.25 npm run build
```

Unset, or not a positive number, it is 60.

The frontend has no test runner. The player was checked by hand in a headless browser, against a backend running on the fake LLM, the fake voice and the fake Jamendo source of the Python tests.

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

The replay page is the same app built read-only. It plays shows exported as static files, so it needs no backend and runs on GitHub Pages. The create screen is hidden and the shows are listed from `public/shows/index.json`. A catalog or Jamendo show of a set length plays as one MP3. A YouTube show plays segment by segment: the host's speech files are static files too, and the songs play in the YouTube player, so the visitor's browser talks to YouTube. An endless show that the host closed plays segment by segment as well; on Jamendo its songs are static files like the speech. The replay page never makes a show grow. Each show credits its tracks with their own license, and the footer credits ElevenLabs for the voice.

```sh
npm run build:replay
```

This sets `VITE_REPLAY=1` through `.env.replay` and builds with the base path `/Radio-Llama/`, the path of a GitHub Pages project site.

The shows come from the backend's output folder. From the repository root:

```sh
uv run radio-llama export-replay <show_id>... --zip replay-shows.zip
```

This copies each ready show's `show.json` and its audio into `frontend/public/shows/<id>/`, rewrites `index.json`, and packs the folder into `replay-shows.zip`. The audio is `show.mp3` for a catalog or Jamendo show of a set length and one speech file per host segment (`00-intro.mp3`, `02-transition.mp3`, ...) for a YouTube show, which has no MP3. An endless Jamendo show has one file per segment, songs included (`01-song.mp3`, ...). An endless show exports only once the host closed it: one that is still open is refused. That folder and the zip are git-ignored: audio is never committed.

`.github/workflows/pages.yml` deploys the replay build when a `v*` tag is pushed. It downloads `replay-shows.zip` from the release of that tag, unpacks it into `public/shows/`, runs `npm run build:replay` and publishes `dist/` to Pages. The release must carry the zip, and Pages must be enabled with GitHub Actions as the source. A new set of shows, YouTube shows included, needs a new `v*` release with its own `replay-shows.zip`.
