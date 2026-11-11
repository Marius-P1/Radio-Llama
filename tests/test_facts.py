"""Facts from MusicBrainz, Wikidata and Wikipedia, on recorded replies (D029)."""

from pathlib import Path

import httpx
import pytest

from radio_llama import facts
from radio_llama.facts import USER_AGENT, cache_key, musicbrainz_artist_fact
from tests.conftest import FakeFactServices

KEVIN_MBID = "8292bf44-2dd1-4bad-8019-276ec38f28e7"
MUSICBRAINZ_FACT = "Kevin MacLeod is a solo artist from the United States, born in 1972."
CHAIN = [
    "musicbrainz_artist_search",
    "musicbrainz_artist",
    "wikidata_entity",
]


def test_full_chain_in_english(fake_facts: FakeFactServices) -> None:
    found = fake_facts.lookup.artist_facts("Kevin MacLeod", "en")

    assert [fact.text for fact in found] == [
        "Kevin MacLeod is an American composer and music producer.",
        "Described by The New York Times as \"arguably the most prolific composer you've never "
        'heard of", MacLeod has composed over 2,000 pieces of royalty-free library music and '
        "made them available under a Creative Commons copyright license.",
        MUSICBRAINZ_FACT,
    ]
    wikipedia, musicbrainz = found[:2], found[2]
    for fact in wikipedia:
        assert fact.about == "artist"
        assert fact.source_type == "wikipedia"
        assert fact.source_url == "https://en.wikipedia.org/wiki/Kevin_MacLeod"
        assert fact.quote == fact.text
    assert musicbrainz.about == "artist"
    assert musicbrainz.source_type == "musicbrainz"
    assert musicbrainz.source_url == f"https://musicbrainz.org/artist/{KEVIN_MBID}"
    assert musicbrainz.quote == "Type: Person. Area: United States. Begin: 1972-09-28"
    assert fake_facts.routes() == [*CHAIN, "wikipedia_summary_en"]


def test_requests_follow_the_matching_chain(fake_facts: FakeFactServices) -> None:
    fake_facts.lookup.artist_facts('Kevin "KM" MacLeod', "en")

    search, artist, wikidata, summary = fake_facts.requests
    assert search.url.params["query"] == 'artist:"Kevin \\"KM\\" MacLeod"'
    assert search.url.params["fmt"] == "json"
    assert artist.url.path == f"/ws/2/artist/{KEVIN_MBID}"
    assert artist.url.params["inc"] == "url-rels"
    assert wikidata.url.path == "/wiki/Special:EntityData/Q16731782.json"
    assert summary.url.path == "/api/rest_v1/page/summary/Kevin_MacLeod"
    for request in fake_facts.requests:
        assert request.headers["User-Agent"] == USER_AGENT


def test_full_chain_in_french(fake_facts: FakeFactServices) -> None:
    found = fake_facts.lookup.artist_facts("Kevin MacLeod", "fr")

    assert len(found) == 3
    assert found[0].text == (
        "Kevin MacLeod est un compositeur et producteur musical américain né le 28 septembre "
        "1972 à Green Bay."
    )
    assert found[1].text.startswith("Décrit comme « le compositeur le plus prolifique")
    assert found[0].source_url == "https://fr.wikipedia.org/wiki/Kevin_MacLeod_(musicien)"
    # The MusicBrainz sentence is written in English; the host says it in French.
    assert found[2].text == MUSICBRAINZ_FACT
    # The title has spaces and brackets: encoded in the path.
    assert fake_facts.requests[-1].url.raw_path.decode() == (
        "/api/rest_v1/page/summary/Kevin_MacLeod_%28musicien%29"
    )


def test_a_page_in_the_other_language_is_used_when_the_show_language_has_none(
    fake_facts: FakeFactServices,
) -> None:
    del fake_facts.replies["wikidata_entity"]["entities"]["Q16731782"]["sitelinks"]["enwiki"]

    found = fake_facts.lookup.artist_facts("Kevin MacLeod", "en")

    assert found[0].source_url == "https://fr.wikipedia.org/wiki/Kevin_MacLeod_(musicien)"


def test_a_low_score_gives_no_facts(fake_facts: FakeFactServices) -> None:
    # The two other Kevin MacLeods of the recorded search score 73.
    search = fake_facts.replies["musicbrainz_artist_search"]
    search["artists"] = search["artists"][1:]

    assert fake_facts.lookup.artist_facts("Kevin MacLeod", "en") == []
    assert fake_facts.routes() == ["musicbrainz_artist_search"]


def test_no_search_result_gives_no_facts(fake_facts: FakeFactServices) -> None:
    fake_facts.replies["musicbrainz_artist_search"]["artists"] = []

    assert fake_facts.lookup.artist_facts("Nobody Knows Me", "en") == []


def test_no_wikidata_link_gives_only_the_musicbrainz_fact(fake_facts: FakeFactServices) -> None:
    artist = fake_facts.replies["musicbrainz_artist"]
    artist["relations"] = [r for r in artist["relations"] if r["type"] != "wikidata"]

    found = fake_facts.lookup.artist_facts("Kevin MacLeod", "en")

    assert [fact.text for fact in found] == [MUSICBRAINZ_FACT]
    assert fake_facts.routes() == CHAIN[:2]


def test_a_disambiguation_page_is_skipped(fake_facts: FakeFactServices) -> None:
    disambiguation = fake_facts.replies["wikipedia_summary_disambiguation"]
    fake_facts.replies["wikipedia_summary_en"] = disambiguation

    found = fake_facts.lookup.artist_facts("Kevin MacLeod", "en")

    assert [fact.text for fact in found] == [MUSICBRAINZ_FACT]


@pytest.mark.parametrize("route", ["musicbrainz_artist", "wikidata_entity", "wikipedia_summary_en"])
def test_an_http_error_gives_no_facts(fake_facts: FakeFactServices, route: str) -> None:
    fake_facts.errors[route] = 500

    assert fake_facts.lookup.artist_facts("Kevin MacLeod", "en") == []


def test_a_busy_musicbrainz_is_tried_twice_then_gives_no_facts(
    fake_facts: FakeFactServices, caplog: pytest.LogCaptureFixture
) -> None:
    fake_facts.errors["musicbrainz_artist_search"] = 503

    assert fake_facts.lookup.artist_facts("Kevin MacLeod", "en") == []
    assert fake_facts.routes() == ["musicbrainz_artist_search"] * 2
    assert "HTTP 503" in caplog.text


def test_a_timeout_gives_no_facts(fake_facts: FakeFactServices) -> None:
    fake_facts.errors["wikidata_entity"] = httpx.ReadTimeout("too slow")

    assert fake_facts.lookup.artist_facts("Kevin MacLeod", "en") == []


def test_a_reply_that_is_not_json_gives_no_facts(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>busy</html>"))
    lookup = facts.FactLookup(tmp_path, http=httpx.Client(transport=transport))

    assert lookup.artist_facts("Kevin MacLeod", "en") == []


def test_an_unexpected_shape_gives_no_facts(fake_facts: FakeFactServices) -> None:
    fake_facts.replies["wikidata_entity"] = {"entities": "none"}

    assert fake_facts.lookup.artist_facts("Kevin MacLeod", "en") == []


def test_a_failed_lookup_is_not_cached(fake_facts: FakeFactServices) -> None:
    fake_facts.errors["wikipedia_summary_en"] = 500
    assert fake_facts.lookup.artist_facts("Kevin MacLeod", "en") == []

    del fake_facts.errors["wikipedia_summary_en"]

    assert len(fake_facts.lookup.artist_facts("Kevin MacLeod", "en")) == 3


def test_a_cached_result_makes_no_request(fake_facts: FakeFactServices, tmp_path: Path) -> None:
    first = fake_facts.lookup.artist_facts("Kevin MacLeod", "en")
    fake_facts.requests.clear()

    again = fake_facts.lookup.artist_facts("  kevin macleod ", "en")

    assert again == first
    assert fake_facts.requests == []
    assert (tmp_path / "cache" / "facts" / f"{cache_key('Kevin MacLeod', 'en')}.json").exists()
    # Another language is another Wikipedia page: a new lookup.
    fake_facts.lookup.artist_facts("Kevin MacLeod", "fr")
    assert fake_facts.requests != []


def test_a_cached_miss_makes_no_request(fake_facts: FakeFactServices) -> None:
    fake_facts.replies["musicbrainz_artist_search"]["artists"] = []
    assert fake_facts.lookup.artist_facts("Nobody Knows Me", "en") == []
    fake_facts.requests.clear()

    assert fake_facts.lookup.artist_facts("Nobody Knows Me", "en") == []
    assert fake_facts.requests == []


def test_musicbrainz_requests_are_a_second_apart(
    fake_facts: FakeFactServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(facts, "MUSICBRAINZ_PAUSE_S", 1.0)
    sleeps: list[float] = []
    # A clock that stands still: every reply comes back instantly.
    monkeypatch.setattr(facts.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(facts.time, "sleep", sleeps.append)

    fake_facts.lookup.artist_facts("Kevin MacLeod", "en")

    # The search goes at once; the artist call waits a full second. Wikimedia has no wait.
    assert sleeps == [1.0]
    assert len(fake_facts.requests) == 4


@pytest.mark.parametrize(
    ("artist", "sentence"),
    [
        (
            {"type": "Group", "area": {"name": "France"}, "life-span": {"begin": "2004-03"}},
            "X is a group from France, formed in 2004.",
        ),
        (
            {"type": "Group", "life-span": {"begin": "1990", "ended": True}},
            "X was a group, formed in 1990.",
        ),
        ({"type": "Other", "area": {"name": "Japan"}}, "X is an artist from Japan."),
        ({"type": None, "life-span": {"begin": "2011"}}, "X is an artist, active since 2011."),
        (
            {"type": "Person", "area": {"name": "United Kingdom"}},
            "X is a solo artist from the United Kingdom.",
        ),
    ],
)
def test_musicbrainz_artist_sentence(artist: dict, sentence: str) -> None:
    fact = musicbrainz_artist_fact({"id": "abc", "name": "X", **artist})

    assert fact is not None
    assert fact.text == sentence
    assert fact.source_url == "https://musicbrainz.org/artist/abc"


def test_musicbrainz_artist_with_only_a_type_gives_no_sentence() -> None:
    assert musicbrainz_artist_fact({"id": "abc", "name": "X", "type": "Person"}) is None
