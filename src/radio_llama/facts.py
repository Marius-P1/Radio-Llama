"""Facts for tracks outside the hand-written catalog, from MusicBrainz and Wikipedia (D029).

The host may only say facts that have a source. The catalog has them; a Jamendo track
only has its artist's own description, often empty. This module looks the artist up:

1. MusicBrainz artist search by name. The top result counts only with a high score and
   the same name, or an alias equal to it: the search scores its best hit 100 whatever it is.
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

from radio_llama import trace
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
# One more try, after the usual pause, often works. The same for a timeout or a lost connection.
MUSICBRAINZ_RETRIES = 1
# A search result below this score (out of 100) is another artist. The best hit always
# scores 100, so its name is checked too.
MIN_SCORE = 90
# Wikipedia editions tried after the show's language. The host rewrites a fact in the show's
# language anyway, so a page in the station's other language is better than none.
WIKIPEDIA_LANGUAGES = ("en", "fr")
WIKIPEDIA_SENTENCES = 2

# A sentence is a usable fact when it has a few words, a sane length and no link.
MIN_FACT_WORDS = 4
MAX_FACT_CHARS = 300
# A sentence ends at ".", "!" or "?" followed by a space and a capital or an opening quote,
# but not after these abbreviations or a single capital (initials such as "U.S." or "J. S.").
_SENTENCE_END = re.compile(r"[.!?]\s+")
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "jr", "sr", "no", "inc", "ltd", "vs", "approx"}
ABBREVIATIONS |= {"c", "b"}  # circa, born
OPENING_QUOTES = "\"'\u201c\u2018\u00ab"

# MusicBrainz artist types, as a few words a sentence can use.
ARTIST_TYPES = {
    "Person": "a solo artist",
    "Group": "a group",
    "Orchestra": "an orchestra",
    "Choir": "a choir",
}
# Area names that read better with "the" in front, besides those `area_name` recognises.
AREAS_WITH_THE = {
    "Netherlands",
    "Philippines",
    "Bahamas",
    "Gambia",
    "Maldives",
    "Isle of Man",
    "Vatican City",
    "Czech Republic",
    "Dominican Republic",
    "Central African Republic",
    "Democratic Republic of the Congo",
}

_FACT_LIST = TypeAdapter(list[Fact])


class FactLookupError(Exception):
    """A service answered with an error. Caught inside this module, never raised to callers."""


# What a failed lookup raises: an HTTP error or timeout, bad JSON, or an unexpected shape.
LOOKUP_FAILURES = (
    FactLookupError,
    httpx.HTTPError,
    ValueError,
    LookupError,
    TypeError,
    AttributeError,
    StopIteration,
)


def split_sentences(text: str) -> list[str]:
    """`text` cut into sentences, without cutting after an abbreviation or an initial."""
    text = " ".join(text.split())
    sentences = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        following = text[match.end() : match.end() + 1]
        if not (following.isupper() or following in OPENING_QUOTES):
            continue
        if match.group().startswith("."):
            word = re.split(r"\W", text[: match.start()])[-1]
            if word.casefold() in ABBREVIATIONS or (len(word) == 1 and word.isupper()):
                continue
        sentences.append(text[start : match.start() + 1])
        start = match.end()
    sentences.append(text[start:])
    return sentences


def usable_sentences(text: str, limit: int) -> list[str]:
    """The first `limit` sentences of `text` with a few words, a sane length and no link."""
    sentences = []
    for sentence in split_sentences(text):
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
        # Every answer of this lookup, failures included, so a show with three songs by one
        # artist does not run a failing chain three times.
        self._answers: dict[str, list[Fact]] = {}

    def artist_facts(self, artist: str, language: str) -> list[Fact]:
        """Sourced facts about the artist, or [] when nothing is found."""
        key = cache_key(artist, language)
        started = time.perf_counter()
        if key in self._answers:
            _record_cached(started, artist, language, "memory", self._answers[key])
        else:
            self._answers[key] = self._find(artist, language, self._dir / f"{key}.json")
        return self._answers[key]

    def _find(self, artist: str, language: str, path: Path) -> list[Fact]:
        """From the disk cache, else from the services. Only a complete answer is cached."""
        if path.exists():
            started = time.perf_counter()
            try:
                found = _FACT_LIST.validate_json(path.read_text(encoding="utf-8"))
            except ValidationError:
                log.warning("Ignoring a broken facts cache file: %s", path)
            else:
                _record_cached(started, artist, language, "disk", found)
                return found
        try:
            data = self._musicbrainz_artist(artist)
            artist_fact = musicbrainz_artist_fact(data) if data is not None else None
        except LOOKUP_FAILURES as exc:
            log.warning("No facts for %s: %s", artist, exc)
            return []
        facts = [artist_fact] if artist_fact is not None else []
        if data is not None:
            try:
                facts = [*self._wikipedia_facts(data, language), *facts]
            except LOOKUP_FAILURES as exc:
                # Keep the MusicBrainz fact, uncached: a later show may reach Wikipedia.
                log.warning("No Wikipedia facts for %s: %s", artist, exc)
                return facts
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([fact.model_dump() for fact in facts], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return facts

    # The lookup chain

    def _musicbrainz_artist(self, artist: str) -> dict | None:
        """The MusicBrainz artist with its URL relations, or None when no hit is that artist."""
        found = self._musicbrainz(
            "artist/", {"query": f'artist:"{lucene_phrase(artist)}"', "limit": "3"}
        )
        candidates = found.get("artists") or []
        if not candidates:
            return None
        top = candidates[0]
        if top.get("score", 0) < MIN_SCORE or not same_artist(artist, top):
            return None
        return self._musicbrainz(f"artist/{top['id']}", {"inc": "url-rels"})

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
        for attempt in range(MUSICBRAINZ_RETRIES + 1):
            if self._last_musicbrainz is not None:
                wait = MUSICBRAINZ_PAUSE_S - (time.monotonic() - self._last_musicbrainz)
                if wait > 0:
                    time.sleep(wait)
            try:
                response = self._send(
                    f"{MUSICBRAINZ_URL}/{path}", {**params, "fmt": "json"}, attempt=attempt + 1
                )
            except httpx.TransportError:  # a timeout or a lost connection
                if attempt == MUSICBRAINZ_RETRIES:
                    raise
                continue
            finally:
                self._last_musicbrainz = time.monotonic()
            if response.status_code != 503:
                break
        return _json(response)

    def _get(self, url: str) -> dict:
        return _json(self._send(url))

    def _send(
        self, url: str, params: dict[str, str] | None = None, attempt: int = 1
    ) -> httpx.Response:
        """One GET, recorded in the trace file of the service the URL names (D037)."""
        service = httpx.URL(url).host.split(".")[-2]  # musicbrainz, wikidata or wikipedia
        sent = {"url": url, "params": params, "attempt": attempt}
        started = time.perf_counter()
        try:
            response = self._http.get(url, params=params, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError as exc:
            trace.record(service, started, error=str(exc), **sent, status=None, body=None)
            raise
        status = response.status_code
        error = None if status == 200 else f"HTTP {status}"
        trace.record(
            service, started, error=error, **sent, status=status, body=trace.response_body(response)
        )
        return response


def _record_cached(
    started: float, artist: str, language: str, where: str, facts: list[Fact]
) -> None:
    """One `facts.jsonl` line for an answer served from the memory or disk cache (D037)."""
    trace.record(
        "facts",
        started,
        artist=artist,
        language=language,
        cached=where,
        facts=[fact.model_dump() for fact in facts],
    )


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
    text = f"{normalized(artist)}\n{language}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def same_artist(name: str, hit: dict) -> bool:
    """Whether a search hit's name, or one of its aliases, is `name`, ignoring case and spaces."""
    aliases = [alias.get("name") or "" for alias in hit.get("aliases") or []]
    return normalized(name) in {normalized(other) for other in [hit.get("name") or "", *aliases]}


def normalized(name: str) -> str:
    return " ".join(name.casefold().split())


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


def area_name(area: str) -> str:
    """The area as said after "from": "the United States", "the Bahamas", "France"."""
    if (
        area.startswith("United ")
        or area.endswith(("Republic", "Islands", "Isles", "Emirates"))
        or area in AREAS_WITH_THE
    ):
        return f"the {area}"
    return area


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
        sentence += f" from {area_name(area)}"
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
