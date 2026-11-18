"""MusicSource on the Jamendo v3.0 API (D029, D030).

Jamendo hosts Creative Commons music and answers plain HTTPS GET requests with JSON. Two
read methods are used: `tracks` for the search and `artists/musicinfo` for the artist
description that becomes the host's facts. The audio is fetched once through the track's
stream URL into `<cache_dir>/jamendo/<id>.mp3`, and from then on the pipeline treats it
like a catalog file. Jamendo has no SDK, so the calls go through httpx.
"""

import html
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from radio_llama import trace
from radio_llama.facts import usable_sentences
from radio_llama.models import Energy, Fact, Track
from radio_llama.music.base import MusicSourceError, UnknownTrackError

log = logging.getLogger(__name__)

API_URL = "https://api.jamendo.com/v3.0"
ID_PREFIX = "jamendo-"
TIMEOUT_S = 60.0
SEARCH_LIMIT = 50  # candidates per search; the pipeline samples a few of them
# How many pages of one query an endless show reads while looking for a song it has not
# played (D044): 500 tracks, far more than a show plays, and a bound on the calls of a step.
MAX_PAGES = 10
# Songs between one and ten minutes: shorter is a jingle, longer is an ambient piece.
DURATION_RANGE = "60_600"
# Jamendo sometimes answers a successful but empty list for a query that has thousands
# of matches (seen live on 2026-11-08, about one call in three). A second try works.
EMPTY_RETRIES = 2
RETRY_PAUSE_S = 0.5
# An endless show cannot take such an empty reply for the end of the music, or the host
# would sign off in the middle of a style with thousands of tracks. Its search asks for
# the total of the query (`fullcount=true`): a real reply carries it, even with no
# results, and a false empty one does not (D044). On 2026-11-18, 37 to 41 % of the calls
# were false empties and they came in runs of four or more, so the page is asked again
# more often and with a longer pause than above: up to six tries over ten seconds. A step
# is asked at the middle of a song, so it has that time.
FALSE_EMPTY_RETRIES = 5
FALSE_EMPTY_PAUSE_S = 2.0
FALSE_EMPTY_ERROR = "Jamendo did not answer the search. Try again in a moment."

# Jamendo's featured genres (from its API docs) plus lofi, each with the tag searched for it.
STYLE_TAGS = {
    "classical": "classical",
    "electronic": "electronic",
    "hiphop": "hiphop",
    "jazz": "jazz",
    "lofi": "lofi",
    "lounge": "lounge",
    "metal": "metal",
    "pop": "pop",
    "relaxation": "relaxation",
    "rock": "rock",
    "songwriter": "songwriter",
    "soundtrack": "soundtrack",
    "world": "world",
}

Speed = Literal["verylow", "low", "medium", "high", "veryhigh"]
ENERGY_SPEEDS: dict[Energy, list[Speed]] = {
    "calm": ["verylow", "low"],
    "medium": ["medium"],
    "upbeat": ["high", "veryhigh"],
}
SPEED_ENERGY: dict[str, Energy] = {
    speed: energy for energy, speeds in ENERGY_SPEEDS.items() for speed in speeds
}

# Languages tried for the artist description, in order. English first, like the catalog facts.
DESCRIPTION_LANGUAGES = ("en", "fr", "es", "de", "it", "pt")
MAX_FACTS = 2

_LICENSE = re.compile(r"creativecommons\.org/licenses/([a-z-]+)/(\d+\.\d+)(?:/([a-z]{2}))?")
_PUBLIC_DOMAIN = re.compile(r"creativecommons\.org/publicdomain/zero/(\d+\.\d+)")
_TAGS = re.compile(r"<[^>]+>")


class JamendoError(MusicSourceError):
    """A Jamendo call failed. The message names the cause."""


class WishFilters(BaseModel):
    """The filters a listener's wish can add to a search. Filled by the LLM (D030)."""

    tags: list[str] = Field(default_factory=list)  # any of them, Jamendo `fuzzytags`
    vocal: Literal["vocal", "instrumental"] | None = None
    lang: str | None = None  # lyrics language, two letters
    search: str | None = None  # an artist or track name, Jamendo free text search


class Query(BaseModel):
    """Every filter of one `tracks` request."""

    tag: str  # the style's tag, required (Jamendo `tags`)
    speed: list[Speed] = Field(default_factory=list)
    filters: WishFilters | None = None

    def params(self) -> dict[str, str]:
        params = {"tags": self.tag}
        if self.speed:
            params["speed"] = " ".join(self.speed)
        if self.filters is not None:
            if self.filters.tags:
                params["fuzzytags"] = " ".join(self.filters.tags)
            if self.filters.vocal is not None:
                params["vocalinstrumental"] = self.filters.vocal
            if self.filters.lang is not None:
                params["lang"] = self.filters.lang
            if self.filters.search is not None:
                params["search"] = self.filters.search
        return params


@dataclass
class ArtistInfo:
    url: str
    facts: list[Fact]


class JamendoSource:
    delivery = "file"
    accepts_wish = True
    needs_facts_lookup = True
    endless = True  # `more_tracks` pages through the search for songs not played yet (D044)

    def __init__(self, client_id: str, cache_dir: Path, http: httpx.Client | None = None) -> None:
        self._client_id = client_id
        self._dir = cache_dir / "jamendo"
        self._http = http if http is not None else httpx.Client(timeout=TIMEOUT_S)
        # Every track seen so far, with its stream URL, so audio_path needs no second call.
        self._tracks: dict[str, Track] = {}
        self._stream_urls: dict[str, str] = {}

    def list_styles(self) -> list[str]:
        return sorted(STYLE_TAGS)

    def list_tracks(self, style: str) -> list[Track]:
        if style not in STYLE_TAGS:
            return []
        return self.search(Query(tag=STYLE_TAGS[style]), style)

    def find_tracks(
        self,
        style: str,
        *,
        energy: Energy | None = None,
        filters: WishFilters | None = None,
        minimum: int,
    ) -> list[Track]:
        """Search with every filter, then relax the query until `minimum` tracks are found.

        Relaxation order: drop the wish filters, then drop the speed. The style tag always
        stays. Results of each step are appended without duplicates, so the strictest
        matches come first; the pipeline's energy tiers (D020) then sort out the rest.
        """
        if style not in STYLE_TAGS:
            raise JamendoError(f"Unknown Jamendo style {style!r}.")
        speed = ENERGY_SPEEDS[energy] if energy is not None else []
        steps = [Query(tag=STYLE_TAGS[style], speed=speed, filters=filters)]
        if filters is not None:
            steps.append(Query(tag=STYLE_TAGS[style], speed=speed))
        if speed:
            steps.append(Query(tag=STYLE_TAGS[style]))
        found: list[Track] = []
        seen: set[str] = set()
        for query in steps:
            if len(found) >= minimum:
                break
            for track in self.search(query, style):
                if track.id not in seen:
                    seen.add(track.id)
                    found.append(track)
        return found

    def more_tracks(
        self,
        style: str,
        *,
        energy: Energy | None = None,
        filters: WishFilters | None = None,
        exclude: set[str],
    ) -> list[Track]:
        """Tracks whose id is not in `exclude`, for a show that keeps asking for one more (D044).

        The same relaxation as `find_tracks`: every filter, then no wish filters, then no
        speed. Each query is read page by page until a page brings a track the show has
        not played, at most MAX_PAGES pages. The first such page is the answer, so the
        strictest query that still has something new wins. An empty list means the style
        has nothing left for this show: every query was read to its true end.

        Raises JamendoError when Jamendo keeps answering nothing for a page (a false
        empty reply). That is never read as the end of a query, so it can neither close
        the show nor drop the wish filters or the energy for this step.

        A long show reads one more page per SEARCH_LIMIT songs it played, so its steps
        cost a little more as it goes on.
        """
        if style not in STYLE_TAGS:
            raise JamendoError(f"Unknown Jamendo style {style!r}.")
        speed = ENERGY_SPEEDS[energy] if energy is not None else []
        steps = [Query(tag=STYLE_TAGS[style], speed=speed, filters=filters)]
        if filters is not None:
            steps.append(Query(tag=STYLE_TAGS[style], speed=speed))
        if speed:
            steps.append(Query(tag=STYLE_TAGS[style]))
        for query in steps:
            for page in range(MAX_PAGES):
                tracks, last = self._page(query, style, offset=page * SEARCH_LIMIT)
                fresh = [track for track in tracks if track.id not in exclude]
                if fresh:
                    return fresh
                if last:
                    break
        return []

    def _page(self, query: Query, style: str, offset: int) -> tuple[list[Track], bool]:
        """One page of a search for `more_tracks`, and whether it is the last of its query.

        The same request as `search`, plus `offset` to skip that many results and
        `fullcount=true`, which makes Jamendo say how many results the whole query has.
        That total tells a real reply from a false empty one (see FALSE_EMPTY_RETRIES),
        and it tells where the query ends. The number of tracks on the page cannot: a
        track with no Creative Commons license is left out, so a full page can look short.
        """
        params = {
            "limit": str(SEARCH_LIMIT),
            "include": "musicinfo licenses",
            "audioformat": "mp32",
            "boost": "popularity_month",
            "durationbetween": DURATION_RANGE,
            "fullcount": "true",
            **query.params(),
        }
        if offset > 0:
            params["offset"] = str(offset)
        reply = self._reply("tracks", params)
        for _ in range(FALSE_EMPTY_RETRIES):
            if not _is_false_empty(reply):
                break
            time.sleep(FALSE_EMPTY_PAUSE_S)
            reply = self._reply("tracks", params)
        if _is_false_empty(reply):
            raise JamendoError(FALSE_EMPTY_ERROR)
        results = reply.get("results", [])
        artists = self._artist_infos({str(raw["artist_id"]) for raw in results})
        return self._remember(results, style, artists), _is_last_page(reply, offset)

    def search(self, query: Query, style: str) -> list[Track]:
        """One `tracks` request plus one `artists/musicinfo` request for the facts."""
        params = {
            "limit": str(SEARCH_LIMIT),
            "include": "musicinfo licenses",
            "audioformat": "mp32",
            "boost": "popularity_month",
            "durationbetween": DURATION_RANGE,
            **query.params(),
        }
        results = self._get("tracks", params)
        for _ in range(EMPTY_RETRIES):
            if results:
                break
            time.sleep(RETRY_PAUSE_S)
            results = self._get("tracks", params)
        artists = self._artist_infos({str(raw["artist_id"]) for raw in results})
        return self._remember(results, style, artists)

    def get_track(self, track_id: str) -> Track:
        if track_id in self._tracks:
            return self._tracks[track_id]
        if not track_id.startswith(ID_PREFIX):
            raise UnknownTrackError(f"No track with id {track_id!r}")
        params = {
            "id": track_id.removeprefix(ID_PREFIX),
            "include": "musicinfo licenses",
            "audioformat": "mp32",
        }
        results = self._get("tracks", params)
        if not results:
            raise UnknownTrackError(f"No track with id {track_id!r}")
        artists = self._artist_infos({str(results[0]["artist_id"])})
        return self._remember(results, _style_of(results[0]), artists)[0]

    def audio_path(self, track: Track) -> Path:
        """The cached MP3, fetched through the stream URL on first use."""
        path = self._dir / f"{track.id}.mp3"  # what to_track sets as audio_file
        if path.exists():
            return path
        if track.id not in self._stream_urls:
            self.get_track(track.id)
        self._download(self._stream_urls[track.id], path)
        return path

    # Internals

    def _remember(
        self, results: list[dict], style: str, artists: dict[str, ArtistInfo]
    ) -> list[Track]:
        tracks = []
        for raw in results:
            track = to_track(raw, style, artists.get(str(raw["artist_id"])))
            if track is None:
                continue
            self._tracks[track.id] = track
            self._stream_urls[track.id] = raw["audio"]
            tracks.append(track)
        return tracks

    def _artist_infos(self, artist_ids: set[str]) -> dict[str, ArtistInfo]:
        if not artist_ids:
            return {}
        results = self._get("artists/musicinfo", {"id": " ".join(sorted(artist_ids))})
        infos = {}
        for raw in results:
            url = raw.get("shareurl") or f"https://www.jamendo.com/artist/{raw['id']}"
            description = (raw.get("musicinfo") or {}).get("description") or {}
            infos[str(raw["id"])] = ArtistInfo(url=url, facts=description_facts(description, url))
        return infos

    def _get(self, method: str, params: dict[str, str]) -> list[dict]:
        """The results of one API call."""
        return self._reply(method, params).get("results", [])

    def _reply(self, method: str, params: dict[str, str]) -> dict:
        """One API call: the whole JSON reply, with its `headers` and its `results`."""
        url = f"{API_URL}/{method}/"
        # The trace (D037) gets the query without the client id, which is a secret. The
        # reply echoes it too, in the audio URLs (`from=app-<client_id>`), so the whole
        # record is redacted.
        sent = {"method": method, "params": {"format": "json", **params}, "redact": self._client_id}
        started = time.perf_counter()
        try:
            response = self._http.get(url, params={"client_id": self._client_id, **sent["params"]})
        except httpx.HTTPError as exc:
            trace.record("jamendo", started, error=str(exc), **sent, status=None, body=None)
            raise JamendoError(f"Jamendo is unreachable: {exc}") from exc
        status = response.status_code
        body = trace.response_body(response)
        error = _reply_error(method, status, body)
        trace.record("jamendo", started, error=error, **sent, status=status, body=body)
        if error is not None:
            raise JamendoError(error)
        return body if isinstance(body, dict) else {}

    def _download(self, url: str, path: Path) -> None:
        sent = {"method": "download", "url": url, "path": str(path), "redact": self._client_id}
        started = time.perf_counter()
        try:
            self._fetch_audio(url, path)
        except (JamendoError, OSError) as exc:  # OSError: the disk, not Jamendo
            trace.record("jamendo", started, error=str(exc), **sent, bytes=None)
            raise
        trace.record("jamendo", started, **sent, bytes=path.stat().st_size)
        log.info("Fetched %s from Jamendo", path.name)

    def _fetch_audio(self, url: str, path: Path) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        try:
            with self._http.stream("GET", url) as response:
                if response.status_code != 200:
                    raise JamendoError(
                        f"Jamendo answered HTTP {response.status_code} for {path.stem}'s audio."
                    )
                with part.open("wb") as file:
                    for chunk in response.iter_bytes():
                        file.write(chunk)
        except httpx.HTTPError as exc:
            raise JamendoError(f"Jamendo audio download failed: {exc}") from exc
        part.replace(path)


def _total(reply: dict) -> int | None:
    """How many results the whole query has, from a reply asked with `fullcount=true`.

    Jamendo sends it as a string in `headers.results_fullcount`, the same number on every
    page. None when the reply does not say.
    """
    value = str(reply.get("headers", {}).get("results_fullcount", ""))
    return int(value) if value.isdigit() else None


def _is_false_empty(reply: dict) -> bool:
    """Whether a `tracks` reply is Jamendo answering nothing, not the end of a query.

    Both have no results. The true end of a query still carries the total; the false
    empty reply does not (measured on the live API on 2026-11-18, D044).
    """
    return not reply.get("results") and _total(reply) is None


def _is_last_page(reply: dict, offset: int) -> bool:
    """Whether the query has no result after the page that starts at `offset`."""
    total = _total(reply)
    if total is None:
        # Results with no total: not seen on the live API. Then only a page that is not
        # full can be taken for the last one.
        return len(reply.get("results", [])) < SEARCH_LIMIT
    return offset + SEARCH_LIMIT >= total


def _style_of(raw: dict) -> str:
    """The first known style among a track's genre tags, else its first genre, else ""."""
    genres = ((raw.get("musicinfo") or {}).get("tags") or {}).get("genres") or []
    for style, tag in STYLE_TAGS.items():
        if tag in genres:
            return style
    return genres[0] if genres else ""


def _reply_error(method: str, status: int, body: object) -> str | None:
    """Why a `method` reply is unusable, or None: a bad status, no JSON object, an API error."""
    if status != 200:
        return f"Jamendo answered HTTP {status} for {method}."
    if not isinstance(body, dict):
        return f"Jamendo answered something other than JSON for {method}."
    headers = body.get("headers", {})
    code = headers.get("code", 0)
    if code != 0:
        return error_message(code, headers.get("error_message", ""))
    return None


def error_message(code: int, message: str) -> str:
    """A readable sentence for a Jamendo error code (their docs, response-codes page)."""
    if code == 5:
        return "Jamendo rejected the client id. Check JAMENDO_CLIENT_ID in .env."
    if code == 6:
        return "Jamendo rate limit exceeded (35,000 requests a month on the free plan)."
    if code == 11:
        return "The Jamendo application behind JAMENDO_CLIENT_ID is suspended."
    return f"Jamendo error {code}: {message}"


def to_track(raw: dict, style: str, artist: ArtistInfo | None) -> Track | None:
    """Map one `tracks` result to a Track. None when the license is not a Creative Commons one."""
    license_label = parse_license(raw.get("license_ccurl") or "")
    if license_label is None:
        log.warning("Skipping Jamendo track %s: no Creative Commons license URL", raw.get("id"))
        return None
    jamendo_id = str(raw["id"])
    musicinfo = raw.get("musicinfo") or {}
    tags = musicinfo.get("tags") or {}
    release = raw.get("releasedate") or ""
    return Track(
        id=f"{ID_PREFIX}{jamendo_id}",
        title=raw["name"],
        artist=raw["artist_name"],
        style=style,
        energy=SPEED_ENERGY.get(musicinfo.get("speed", "")),
        album=raw.get("album_name") or "",
        year=int(release[:4]) if release[:4].isdigit() else None,
        duration_s=float(raw["duration"]),
        license=license_label,
        license_url=raw["license_ccurl"],
        track_url=raw["shareurl"],
        artist_url=artist.url if artist else f"https://www.jamendo.com/artist/{raw['artist_id']}",
        audio_file=f"{ID_PREFIX}{jamendo_id}.mp3",
        tags=[*(tags.get("genres") or []), *(tags.get("vartags") or [])],
        facts=artist.facts if artist else [],
    )


def parse_license(url: str) -> str | None:
    """Turn a Creative Commons license URL into the label the catalog uses, such as
    "CC BY-NC-SA 3.0 US" or "CC0 1.0". None when the URL is not a Creative Commons one."""
    match = _LICENSE.search(url)
    if match:
        code, version, jurisdiction = match.groups()
        label = f"CC {code.upper()} {version}"
        return f"{label} {jurisdiction.upper()}" if jurisdiction else label
    match = _PUBLIC_DOMAIN.search(url)
    if match:
        return f"CC0 {match.group(1)}"
    return None


def description_facts(description: dict[str, str], source_url: str) -> list[Fact]:
    """One Fact per usable sentence of the artist description, at most MAX_FACTS.

    The description is HTML written by the artist. The first language with text wins,
    English first. Sentences are picked like Wikipedia's (`facts.usable_sentences`).
    """
    text = ""
    for language in (*DESCRIPTION_LANGUAGES, *sorted(description)):
        if description.get(language, "").strip():
            text = description[language]
            break
    plain = html.unescape(_TAGS.sub(" ", text))
    return [
        Fact(
            text=sentence,
            about="artist",
            source_url=source_url,
            quote=sentence,
            source_type="jamendo_artist_page",
        )
        for sentence in usable_sentences(plain, MAX_FACTS)
    ]
