# Catalog

## What this is

The music Radio Llama plays comes from 48 hand-picked Creative Commons tracks from the Free Music Archive, described in `catalog.json`: 12 or 13 tracks per style, each with its album, year, tags, an energy label, two or three sourced facts and curator notes. The audio files are never committed to git. They are published as a GitHub Release asset and fetched with `download.py`.

The twelve `fake-*` placeholder tracks used before the real catalog live in `tests/fixtures/catalog/`. The tests and CI use them, with sine tones generated on the fly. They are not part of this catalog.

## License policy

Any Creative Commons license that allows redistribution with attribution is accepted: CC BY, CC BY-SA, CC BY-NC and CC BY-NC-SA, in any version. NC tracks are accepted because Radio Llama is a non-commercial demo and says so. ND licenses are excluded.

A show is a collection of tracks, not an adaptation of them: cutting, fading and normalising a song does not make a derivative work. Each track keeps its own license in the credits of every show, so the license mix inside a show is not checked.

The license is written exactly as the source page shows it, for example `CC BY 4.0` or `CC BY-NC-SA 3.0 US`, and `license_url` points at the matching page on creativecommons.org. `tests/test_catalog_file.py` holds the allow-list of license strings and their URLs. When two sources disagree on a track's license, the stricter one is used and the notes say so.

## Schema

`catalog.json` is `{"version": 1, "tracks": [...]}`. Each track follows the `Track` model in `src/radio_llama/models.py`.

Track fields:

- `id` (string): `<style>-<nn>`, for example `jazz-07`. Unique and stable, used in shows and URLs.
- `style` (string): one of `electronic`, `jazz`, `lofi`, `rock`.
- `energy` (string, optional): `calm`, `medium` or `upbeat`. Absent when unknown. A listener can ask for an energy and tracks with that energy are picked first.
- `title` (string): the track title as the source page shows it.
- `spoken_title` (string, optional): what the host says when the title is not speakable as is, for example when it carries a catalog id or spaced letters. The web app shows it too.
- `artist` (string): the artist name as the source page shows it.
- `album` (string): the album or release the track comes from.
- `year` (integer or null): the release year. `null` when the only date available is an upload date.
- `duration_s` (number): the full length of the track in seconds.
- `license` (string): the license as the source page shows it. See the license policy.
- `license_url` (string): the creativecommons.org page for that license.
- `track_url` (string): the page the track was found on. Used for the source link in the credits.
- `audio_file` (string): the file name inside `catalog/audio/`. See the naming rule.
- `artist_url` (string): the artist's page. Used for the artist link in the credits.
- `tags` (list of strings): genre and mood words, fed to the host.
- `facts` (list of Fact): two or three facts about the track or the artist, each with its source and quote.
- `host_notes` (string, optional): one or two sentences of on-air guidance for the host, in English. The transition prompt receives them. Set them only where the curator notes call for it.
- `notes` (string): curator notes, for the people maintaining the catalog. They never reach `show.json`, the API or the replay page.

Fact fields:

- `text` (string): one plain sentence the host can say on air.
- `about` (string): `artist` or `track`. The prompt labels each fact with it.
- `source_url` (string): the page the fact was read on. Must start with `https://`.
- `quote` (string): the source text the fact is written from, copied as written.
- `source_type` (string): what the source is, one of `platform_bio`, `wikipedia`, `bandcamp`, `review`, `interview`, `label`.

Every URL starts with `https://`.

## Naming rule for audio files

`Artist-Title.mp3`, or `Artist-Title-Qualifier.mp3` when the title carries a parenthetical qualifier. Each part is PascalCase and ASCII only: no spaces, apostrophes or accents. Catalog ids such as `(ID 1296)` are dropped. Examples: `BrokeForFree-NightOwl.mp3`, `LoboLoco-DreamyFeets-Instrumental.mp3`.

## Example entry

```json
{
  "id": "electronic-01",
  "style": "electronic",
  "energy": "upbeat",
  "title": "Night Owl",
  "artist": "Broke For Free",
  "album": "Directionless EP",
  "year": 2011,
  "duration_s": 194,
  "license": "CC BY 3.0",
  "license_url": "https://creativecommons.org/licenses/by/3.0/",
  "track_url": "https://freemusicarchive.org/music/Broke_For_Free/Directionless_EP/Broke_For_Free_-_Directionless_EP_-_01_Night_Owl/",
  "audio_file": "BrokeForFree-NightOwl.mp3",
  "artist_url": "https://brokeforfree.bandcamp.com/",
  "tags": [
    "minimal electronic",
    "mellow"
  ],
  "facts": [
    {
      "text": "Night Owl is from the Directionless EP, which the artist described as a collection of their favorite mellow tracks.",
      "about": "track",
      "source_url": "https://freemusicarchive.org/music/Broke_For_Free/Directionless_EP/",
      "quote": "'Directionless' is a collection of my favorite mellow tracks over the last 6 months",
      "source_type": "platform_bio"
    },
    {
      "text": "Broke For Free makes nostalgic, feel-good instrumentals.",
      "about": "artist",
      "source_url": "https://freemusicarchive.org/music/Broke_For_Free/",
      "quote": "Nostalgic, feel-good instrumentals from Northern California",
      "source_type": "platform_bio"
    },
    {
      "text": "Broke For Free is based in Oakland, California.",
      "about": "artist",
      "source_url": "https://brokeforfree.bandcamp.com/music",
      "quote": "Oakland, California",
      "source_type": "bandcamp"
    }
  ],
  "notes": "Night Owl is track 1 of the EP (FMA tracklist), but no fact relies on that."
}
```

## Adding a track

1. Check the license on the track page. It must be in the allow-list above. When sources disagree, take the stricter one and say so in `notes`.
2. Write two or three facts from the artist's page, the album notes or a reliable article. Copy the source text into `quote`. Do not guess.
3. Add the MP3 to `catalog/audio/` under the naming rule, and set `duration_s` from the file.
4. Run `uv run pytest tests/test_catalog_file.py`. It checks the licenses, the license URLs, the file names, the facts and the styles.

## Publishing the audio

1. From inside `catalog/audio/`, zip the files as `catalog-audio.zip`, excluding `fake-*.mp3`. The files must sit at the root of the archive, not in a subfolder.
2. Publish `catalog-audio.zip` as a GitHub Release asset with the tag `catalog-v1`. `download.py` expects that tag and file name.
3. Compute its SHA-256 (`sha256sum catalog-audio.zip`) and write one line into `SHA256SUMS`: `<sha256>  catalog-audio.zip` (two spaces).
4. Commit `SHA256SUMS`.

## Getting the audio locally

Run `uv run python catalog/download.py`. It downloads the release archive, checks it against `SHA256SUMS`, and unpacks it into `catalog/audio/`. Until the `catalog-v1` release exists, the download fails.

To run the pipeline without the real audio, point it at the fixture catalog: generate the tones with `uv run python tests/fixtures/catalog/make_placeholder_audio.py`, then set `CATALOG_PATH=tests/fixtures/catalog/catalog.json`. That is what CI does.
