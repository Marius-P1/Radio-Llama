# Catalog

## What this is

The music Radio Llama plays comes from a small, hand-picked set of Creative Commons tracks described in `catalog.json`. Only CC-BY and CC-BY-SA tracks are accepted, because cutting and fading a song counts as an adaptation. The audio files are never committed to git. They are published as a GitHub Release asset and fetched with `download.py`.

The catalog in the repo today holds placeholder tracks (`fake-*`) with sine-tone audio. They exist so the pipeline runs end to end before the real tracks are chosen.

## Schema

`catalog.json` is `{"version": 1, "tracks": [...]}`. Each track follows the `Track` model in `src/radio_llama/models.py`.

Track fields:

- `id` (string): unique, stable id used in shows and URLs. Lowercase, hyphens, no spaces.
- `title` (string): track title as shown on the source page.
- `artist` (string): artist name as shown on the source page.
- `style` (string): one of `jazz`, `rock`, `electronic`, `lofi`.
- `duration_s` (number): full length of the track in seconds.
- `license` (string): SPDX-like id. One of `CC-BY-4.0`, `CC-BY-SA-4.0`, `CC-BY-3.0`, `CC-BY-SA-3.0`.
- `source_url` (string): page where the track was found. Must start with `https://`. Used for credits.
- `audio_file` (string): file name inside `catalog/audio/`. Must end with `.mp3`.
- `facts` (list of Fact): two or three facts about the track or artist. At least one is required.
- `placeholder` (boolean, default `false`): `true` only for the fake tracks. Real tracks omit it.

Fact fields:

- `text` (string): one plain sentence the host can say on air.
- `source_url` (string): page the fact was read on. Must start with `https://`.

## Example entry

```json
{
  "id": "example-artist-blue-morning",
  "title": "Blue Morning",
  "artist": "Example Artist",
  "style": "jazz",
  "duration_s": 201.5,
  "license": "CC-BY-4.0",
  "source_url": "https://freemusicarchive.org/music/example-artist/blue-morning",
  "audio_file": "example-artist-blue-morning.mp3",
  "facts": [
    {
      "text": "Example Artist recorded this track at home with a single microphone.",
      "source_url": "https://freemusicarchive.org/music/example-artist"
    },
    {
      "text": "The track opens the album Early Light, released in 2021.",
      "source_url": "https://freemusicarchive.org/music/example-artist/early-light"
    }
  ]
}
```

## Replacing the placeholders

1. Delete every `fake-*` entry from `catalog.json`.
2. Add the real tracks. Target: 4 styles x 6 tracks. Each track gets 2 or 3 facts, and every fact has its own `source_url`. Write the facts yourself from the artist's page. Do not guess.
3. Put the MP3 files in `catalog/audio/`, named exactly as each track's `audio_file`.
4. From inside `catalog/audio/`, zip the files as `catalog-audio.zip`. The files must sit at the root of the archive, not in a subfolder.
5. Publish `catalog-audio.zip` as a GitHub Release asset with the tag `catalog-v1`. `download.py` expects that tag and file name.
6. Compute its SHA-256 (`sha256sum catalog-audio.zip`) and write one line into `SHA256SUMS`: `<sha256>  catalog-audio.zip` (two spaces).
7. Run `RELEASE_CHECK=1 uv run pytest`. It fails while any placeholder track remains and lists their ids.
8. Run `uv run pytest` as usual. `tests/test_catalog_file.py` checks the licenses, URLs, durations and facts.

## Getting the audio locally

Run `uv run python catalog/download.py`.

While the catalog holds only placeholders, the script generates a 25 second sine tone per track with ffmpeg into `catalog/audio/`. Nothing is downloaded. Once real tracks are in the catalog, the same command downloads the release archive, checks it against `SHA256SUMS`, and unpacks it into `catalog/audio/`.

To regenerate the placeholder tones, run `uv run python catalog/make_placeholder_audio.py --force`.
