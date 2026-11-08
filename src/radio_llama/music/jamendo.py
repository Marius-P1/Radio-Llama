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
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from radio_llama.models import Energy, Fact, Track
from radio_llama.music.base import MusicSourceError, UnknownTrackError

log = logging.getLogger(__name__)

API_URL = "https://api.jamendo.com/v3.0"
ID_PREFIX = "jamendo-"
TIMEOUT_S = 60.0
SEARCH_LIMIT = 50  # candidates per search; the pipeline samples a few of them
# Songs between one and ten minutes: shorter is a jingle, longer is an ambient piece.
DURATION_RANGE = "60_600"

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
MIN_FACT_WORDS = 4
MAX_FACT_CHARS = 300

_LICENSE = re.compile(r"creativecommons\.org/licenses/([a-z-]+)/(\d+\.\d+)(?:/([a-z]{2}))?")
_PUBLIC_DOMAIN = re.compile(r"creativecommons\.org/publicdomain/zero/(\d+\.\d+)")
_TAGS = re.compile(r"<[^>]+>")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


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
        path = self._dir / track.audio_file
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
        url = f"{API_URL}/{method}/"
        try:
            response = self._http.get(
                url, params={"client_id": self._client_id, "format": "json", **params}
            )
        except httpx.HTTPError as exc:
            raise JamendoError(f"Jamendo is unreachable: {exc}") from exc
        if response.status_code != 200:
            raise JamendoError(f"Jamendo answered HTTP {response.status_code} for {method}.")
        body = response.json()
        headers = body.get("headers", {})
        code = headers.get("code", 0)
        if code != 0:
            raise JamendoError(error_message(code, headers.get("error_message", "")))
        return body.get("results", [])

    def _download(self, url: str, path: Path) -> None:
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
        log.info("Fetched %s from Jamendo", path.name)


def _style_of(raw: dict) -> str:
    """The first known style among a track's genre tags, else its first genre, else ""."""
    genres = ((raw.get("musicinfo") or {}).get("tags") or {}).get("genres") or []
    for style, tag in STYLE_TAGS.items():
        if tag in genres:
            return style
    return genres[0] if genres else ""


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
    English first. A sentence is usable when it has a few words, no URL and a sane length.
    """
    text = ""
    for language in (*DESCRIPTION_LANGUAGES, *sorted(description)):
        if description.get(language, "").strip():
            text = description[language]
            break
    plain = html.unescape(_TAGS.sub(" ", text))
    plain = " ".join(plain.split())
    facts = []
    for sentence in _SENTENCE_END.split(plain):
        sentence = sentence.strip()
        words = sentence.split()
        if len(words) < MIN_FACT_WORDS or len(sentence) > MAX_FACT_CHARS or "http" in sentence:
            continue
        facts.append(
            Fact(
                text=sentence,
                about="artist",
                source_url=source_url,
                quote=sentence,
                source_type="jamendo_artist_page",
            )
        )
        if len(facts) == MAX_FACTS:
            break
    return facts
