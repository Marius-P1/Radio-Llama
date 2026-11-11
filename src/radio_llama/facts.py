"""Facts for tracks outside the hand-written catalog, from MusicBrainz and Wikipedia (D029).

The host may only say facts that have a source. The catalog has them; a Jamendo track
only has its artist's own description, often empty. This module looks the artist up:

1. MusicBrainz artist search by name. The top result counts only with a high score.
2. The MusicBrainz artist with its URL relations: one sentence from its type, area and
   start date, and the Wikidata id when there is one.
3. Wikidata: the title of the artist's Wikipedia page, in the show's language first.
4. The Wikipedia page summary: its first sentences, quoted with the page as the source.

No recording lookup: the host already has the year from Jamendo, and the first release date
MusicBrainz knows for a Creative Commons track is often a late compilation (checked live on
2026-11-11: "Monkeys Spinning Monkeys" by Kevin MacLeod, from about 2013, is listed as 2024).

There is no fuzzy name search on Wikipedia: a name only reaches Wikipedia through the
MusicBrainz match and its Wikidata link. Results, misses included, are cached on disk in
`<cache_dir>/facts/`. A failed lookup logs a warning and gives no facts; a show never fails
because of it. These services have no SDK, so the calls go through httpx.
"""

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError

from radio_llama.models import Fact

log = logging.getLogger(__name__)

# One User-Agent for every service, as MusicBrainz and Wikimedia ask. Never an email address.
USER_AGENT = "RadioLlama/1.5 (https://github.com/Marius-P1/Radio-Llama)"
TIMEOUT_S = 20.0
MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2"
WIKIDATA_URL = "https://www.wikidata.org/wiki/Special:EntityData"
# MusicBrainz allows one request per second per client.
MUSICBRAINZ_PAUSE_S = 1.0
# MusicBrainz answers 503 when it is busy (seen live on 2026-11-11, about one call in three).
# One more try, after the usual pause, often works.
MUSICBRAINZ_RETRIES = 1
# A search result below this score (out of 100) is another artist.
MIN_SCORE = 90
# Wikipedia editions tried after the show's language. The host rewrites a fact in the show's
# language anyway, so a page in the station's other language is better than none.
WIKIPEDIA_LANGUAGES = ("en", "fr")
WIKIPEDIA_SENTENCES = 2

# A sentence is a usable fact when it has a few words, a sane length and no link.
MIN_FACT_WORDS = 4
MAX_FACT_CHARS = 300
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# MusicBrainz artist types, as a few words a sentence can use.
ARTIST_TYPES = {
    "Person": "a solo artist",
    "Group": "a group",
    "Orchestra": "an orchestra",
    "Choir": "a choir",
}
# Area names that read better with "the" in front.
AREAS_WITH_THE = {"United States", "United Kingdom", "Netherlands", "Philippines"}

_FACT_LIST = TypeAdapter(list[Fact])


class FactLookupError(Exception):
    """A service answered with an error. Caught inside this module, never raised to callers."""


def usable_sentences(text: str, limit: int) -> list[str]:
    """The first `limit` sentences of `text` with a few words, a sane length and no link."""
    sentences = []
    for sentence in _SENTENCE_END.split(" ".join(text.split())):
        sentence = sentence.strip()
        if (
            len(sentence.split()) < MIN_FACT_WORDS
            or len(sentence) > MAX_FACT_CHARS
            or "http" in sentence
        ):
            continue
        sentences.append(sentence)
        if len(sentences) == limit:
            break
    return sentences


class FactLookup:
    def __init__(self, cache_dir: Path, http: httpx.Client | None = None) -> None:
        self._dir = cache_dir / "facts"
        self._http = (
            http if http is not None else httpx.Client(timeout=TIMEOUT_S, follow_redirects=True)
        )
        self._last_musicbrainz: float | None = None  # time.monotonic() of the last request

    def artist_facts(self, artist: str, language: str) -> list[Fact]:
        """Sourced facts about the artist, or [] when nothing is found."""
        path = self._dir / f"{cache_key(artist, language)}.json"
        if path.exists():
            try:
                return _FACT_LIST.validate_json(path.read_text(encoding="utf-8"))
            except ValidationError:
                log.warning("Ignoring a broken facts cache file: %s", path)
        try:
            facts = self._look_up(artist, language)
        except (
            FactLookupError,
            httpx.HTTPError,
            ValueError,
            LookupError,
            TypeError,
            AttributeError,
            StopIteration,
        ) as exc:
            # An error, a timeout, bad JSON or an unexpected shape. Not cached: the next show
            # tries again.
            log.warning("No facts for %s: %s", artist, exc)
            return []
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([fact.model_dump() for fact in facts], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return facts

    # The lookup chain

    def _look_up(self, artist: str, language: str) -> list[Fact]:
        found = self._musicbrainz(
            "artist/", {"query": f'artist:"{lucene_phrase(artist)}"', "limit": "3"}
        )
        candidates = found.get("artists") or []
        if not candidates or candidates[0].get("score", 0) < MIN_SCORE:
            return []
        data = self._musicbrainz(f"artist/{candidates[0]['id']}", {"inc": "url-rels"})
        facts = self._wikipedia_facts(data, language)
        artist_fact = musicbrainz_artist_fact(data)
        if artist_fact is not None:
            facts.append(artist_fact)
        return facts

    def _wikipedia_facts(self, artist: dict, language: str) -> list[Fact]:
        """The first sentences of the artist's Wikipedia page, through its Wikidata item."""
        item = wikidata_id(artist)
        if item is None:
            return []
        body = self._get(f"{WIKIDATA_URL}/{item}.json")
        # A merged item redirects to another id, so take the one entity whatever its key.
        entity = next(iter(body["entities"].values()))
        sitelinks = entity.get("sitelinks") or {}
        for wiki_language in (language, *WIKIPEDIA_LANGUAGES):
            link = sitelinks.get(f"{wiki_language}wiki")
            if link is not None:
                return self._summary_facts(wiki_language, link["title"])
        return []

    def _summary_facts(self, language: str, title: str) -> list[Fact]:
        page = quote(title.replace(" ", "_"), safe="")
        summary = self._get(f"https://{language}.wikipedia.org/api/rest_v1/page/summary/{page}")
        if summary.get("type") != "standard":  # a disambiguation page, or no article
            return []
        url = summary["content_urls"]["desktop"]["page"]
        sentences = usable_sentences(summary.get("extract") or "", WIKIPEDIA_SENTENCES)
        return [
            Fact(
                text=sentence,
                about="artist",
                source_url=url,
                quote=sentence,
                source_type="wikipedia",
            )
            for sentence in sentences
        ]

    # HTTP

    def _musicbrainz(self, path: str, params: dict[str, str]) -> dict:
        """A MusicBrainz request, at least MUSICBRAINZ_PAUSE_S after the previous one."""
        for _ in range(MUSICBRAINZ_RETRIES + 1):
            if self._last_musicbrainz is not None:
                wait = MUSICBRAINZ_PAUSE_S - (time.monotonic() - self._last_musicbrainz)
                if wait > 0:
                    time.sleep(wait)
            try:
                response = self._send(f"{MUSICBRAINZ_URL}/{path}", {**params, "fmt": "json"})
            finally:
                self._last_musicbrainz = time.monotonic()
            if response.status_code != 503:
                break
        return _json(response)

    def _get(self, url: str) -> dict:
        return _json(self._send(url))

    def _send(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        return self._http.get(url, params=params, headers={"User-Agent": USER_AGENT})


def _json(response: httpx.Response) -> dict:
    """The JSON object of a successful reply. Anything else raises FactLookupError."""
    if response.status_code != 200:
        raise FactLookupError(f"HTTP {response.status_code} from {response.url.host}")
    body = response.json()
    if not isinstance(body, dict):
        raise FactLookupError(f"unexpected reply from {response.url.host}")
    return body


def cache_key(artist: str, language: str) -> str:
    """A short file name for one lookup. The language picks the Wikipedia edition."""
    text = f"{artist.casefold().strip()}\n{language}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def lucene_phrase(text: str) -> str:
    """Escape a name for a quoted phrase in a MusicBrainz search query."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def wikidata_id(artist: dict) -> str | None:
    """The Wikidata item id (Q...) of a MusicBrainz artist, from its URL relations."""
    for relation in artist.get("relations") or []:
        if relation.get("type") == "wikidata":
            resource = (relation.get("url") or {}).get("resource") or ""
            item = resource.rstrip("/").rsplit("/", 1)[-1]
            if item.startswith("Q"):
                return item
    return None


def musicbrainz_artist_fact(artist: dict) -> Fact | None:
    """One sentence from the artist's type, area and start date. None when they are missing.

    For example "Josh Woodward is a solo artist from the United States." or "X was a group
    from France, formed in 2004." Written in English; the host says it in the show's language.
    """
    kind = artist.get("type")
    area = (artist.get("area") or {}).get("name")
    life_span = artist.get("life-span") or {}
    begin = (life_span.get("begin") or "")[:4]
    if not area and not begin.isdigit():
        return None
    verb = "was" if life_span.get("ended") else "is"
    sentence = f"{artist['name']} {verb} {ARTIST_TYPES.get(kind or '', 'an artist')}"
    if area:
        sentence += f" from {'the ' if area in AREAS_WITH_THE else ''}{area}"
    if begin.isdigit():
        if kind == "Person":
            sentence += f", born in {begin}"
        elif kind in ARTIST_TYPES:
            sentence += f", formed in {begin}"
        else:
            sentence += f", active since {begin}"
    fields = {"Type": kind, "Area": area, "Begin": life_span.get("begin")}
    return Fact(
        text=f"{sentence}.",
        about="artist",
        source_url=f"https://musicbrainz.org/artist/{artist['id']}",
        quote=". ".join(f"{name}: {value}" for name, value in fields.items() if value),
        source_type="musicbrainz",
    )
