"""The Jamendo source on recorded replies (D030). No test here reaches the network."""

import shutil
from pathlib import Path

import httpx
import pytest

from radio_llama.music import (
    JamendoError,
    JamendoSource,
    UnknownTrackError,
    WishFilters,
    jamendo,
)
from radio_llama.music.jamendo import STYLE_TAGS, Query, description_facts, parse_license
from tests.conftest import FakeJamendo

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _params(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


def test_styles_are_jamendo_genres_plus_lofi(fake_jamendo: FakeJamendo) -> None:
    styles = fake_jamendo.source.list_styles()

    assert styles == sorted(STYLE_TAGS)
    assert {"jazz", "rock", "electronic", "lofi", "hiphop", "classical"} <= set(styles)
    assert fake_jamendo.requests == []  # the list is static, no call


def test_list_tracks_maps_the_recorded_reply(fake_jamendo: FakeJamendo) -> None:
    tracks = fake_jamendo.source.list_tracks("jazz")

    assert [track.id for track in tracks] == [
        "jamendo-1001",
        "jamendo-1002",
        "jamendo-1003",
        "jamendo-1004",
    ]
    first = tracks[0]
    assert first.title == "Blue Window"
    assert first.artist == "Example Band"
    assert first.style == "jazz"
    assert first.energy == "upbeat"
    assert first.album == "Night Sessions"
    assert first.year == 2015
    assert first.duration_s == 214.0
    assert first.license == "CC BY-NC-ND 3.0"
    assert first.license_url == "http://creativecommons.org/licenses/by-nc-nd/3.0/"
    assert first.track_url == "https://www.jamendo.com/track/1001"
    assert first.artist_url == "https://www.jamendo.com/artist/2001"
    assert first.audio_file == "jamendo-1001.mp3"
    assert first.tags == ["jazz", "energetic"]
    assert first.notes == ""
    # A single: no album, no release date.
    assert tracks[1].album == ""
    assert tracks[1].year is None
    assert tracks[1].energy == "calm"
    assert tracks[2].energy == "medium"
    assert tracks[3].license == "CC BY-NC-SA 3.0 US"
    assert [track.energy for track in tracks] == ["upbeat", "calm", "medium", "upbeat"]


def test_list_tracks_sends_the_style_tag_and_the_fixed_filters(fake_jamendo: FakeJamendo) -> None:
    fake_jamendo.source.list_tracks("jazz")

    tracks_request, artists_request = fake_jamendo.requests
    params = _params(tracks_request)
    assert tracks_request.url.host == "api.jamendo.com"
    assert tracks_request.url.path == "/v3.0/tracks/"
    assert params["client_id"] == "test-client-id"
    assert params["format"] == "json"
    assert params["tags"] == "jazz"
    assert params["include"] == "musicinfo licenses"
    assert params["audioformat"] == "mp32"
    assert params["limit"] == "50"
    assert params["durationbetween"] == "60_600"
    assert "speed" not in params
    assert artists_request.url.path == "/v3.0/artists/musicinfo/"
    assert _params(artists_request)["id"] == "2001 2002 2003"


def test_unknown_style_lists_nothing_without_a_call(fake_jamendo: FakeJamendo) -> None:
    assert fake_jamendo.source.list_tracks("polka") == []
    assert fake_jamendo.requests == []


def test_artist_description_becomes_sourced_facts(fake_jamendo: FakeJamendo) -> None:
    tracks = {track.id: track for track in fake_jamendo.source.list_tracks("jazz")}

    facts = tracks["jamendo-1001"].facts
    assert [fact.text for fact in facts] == [
        "Formed in Lyon in 2009, Example Band plays jazz with a hip hop pulse.",
        "Their second album came out in 2015 & toured France.",
    ]
    assert all(fact.about == "artist" for fact in facts)
    assert all(fact.source_url == "https://www.jamendo.com/artist/2001" for fact in facts)
    assert all(fact.quote == fact.text for fact in facts)
    assert all(fact.source_type == "jamendo_artist_page" for fact in facts)
    # Same artist, same facts.
    assert tracks["jamendo-1002"].facts == facts
    # English empty: the French text is used.
    assert [fact.text for fact in tracks["jamendo-1003"].facts] == [
        "Chanteuse basée à Montréal, elle écrit des chansons folk depuis 2012."
    ]
    # A description that is only a link gives no fact.
    assert tracks["jamendo-1004"].facts == []


def test_description_facts_skip_short_long_and_link_sentences() -> None:
    long_sentence = "word " * 80 + "end."
    description = {
        "en": f"<p>Hi there.</p> See http://x.y now please. {long_sentence} Four words are enough."
    }

    facts = description_facts(description, "https://www.jamendo.com/artist/1")

    assert [fact.text for fact in facts] == ["Four words are enough."]


def test_description_facts_handle_an_empty_description() -> None:
    assert description_facts({}, "https://x") == []
    assert description_facts({"en": "", "fr": "   "}, "https://x") == []


@pytest.mark.parametrize(
    ("url", "label"),
    [
        ("http://creativecommons.org/licenses/by/3.0/", "CC BY 3.0"),
        ("https://creativecommons.org/licenses/by-nc-sa/3.0/us/", "CC BY-NC-SA 3.0 US"),
        ("http://creativecommons.org/licenses/by-nd/4.0/", "CC BY-ND 4.0"),
        ("http://creativecommons.org/publicdomain/zero/1.0/", "CC0 1.0"),
        ("", None),
        ("https://example.com/license", None),
    ],
)
def test_parse_license(url: str, label: str | None) -> None:
    assert parse_license(url) == label


def test_find_tracks_with_energy_and_wish_sends_every_filter(fake_jamendo: FakeJamendo) -> None:
    filters = WishFilters(tags=["piano", "rainy"], vocal="instrumental", lang="fr", search="Lea")

    tracks = fake_jamendo.source.find_tracks("jazz", energy="calm", filters=filters, minimum=3)

    assert len(tracks) == 4
    params = _params(fake_jamendo.requests[0])
    assert params["tags"] == "jazz"
    assert params["speed"] == "verylow low"
    assert params["fuzzytags"] == "piano rainy"
    assert params["vocalinstrumental"] == "instrumental"
    assert params["lang"] == "fr"
    assert params["search"] == "Lea"
    # Enough tracks at the first step: one tracks call, one artists call.
    assert len(fake_jamendo.requests) == 2


def test_find_tracks_relaxes_the_wish_then_the_speed(fake_jamendo: FakeJamendo) -> None:
    fake_jamendo.reject_params = {"fuzzytags", "speed"}
    filters = WishFilters(tags=["piano"])

    tracks = fake_jamendo.source.find_tracks("jazz", energy="upbeat", filters=filters, minimum=3)

    assert len(tracks) == 4
    tracks_params = [
        _params(request) for request in fake_jamendo.requests if request.url.path == "/v3.0/tracks/"
    ]
    # Each empty step is retried twice before the query is relaxed.
    assert [sorted(set(p) & {"fuzzytags", "speed"}) for p in tracks_params] == [
        ["fuzzytags", "speed"],
        ["fuzzytags", "speed"],
        ["fuzzytags", "speed"],
        ["speed"],
        ["speed"],
        ["speed"],
        [],
    ]


def test_an_empty_reply_is_retried_before_giving_up(fake_jamendo: FakeJamendo) -> None:
    fake_jamendo.empty_replies = 1

    tracks = fake_jamendo.source.list_tracks("jazz")

    assert len(tracks) == 4
    tracks_calls = [r for r in fake_jamendo.requests if r.url.path == "/v3.0/tracks/"]
    assert len(tracks_calls) == 2


def test_a_truly_empty_style_gives_up_after_the_retries(fake_jamendo: FakeJamendo) -> None:
    fake_jamendo.empty_replies = 99

    assert fake_jamendo.source.list_tracks("jazz") == []
    assert len([r for r in fake_jamendo.requests if r.url.path == "/v3.0/tracks/"]) == 3


def test_find_tracks_stops_relaxing_once_enough(fake_jamendo: FakeJamendo) -> None:
    tracks = fake_jamendo.source.find_tracks("jazz", energy="upbeat", minimum=3)

    assert len(tracks) == 4
    assert len([r for r in fake_jamendo.requests if r.url.path == "/v3.0/tracks/"]) == 1


# more_tracks (D044): one more song the show has not played, for an endless show.

ALL_IDS = {"jamendo-1001", "jamendo-1002", "jamendo-1003", "jamendo-1004"}


def _tracks_requests(fake: FakeJamendo) -> list[dict[str, str]]:
    return [_params(r) for r in fake.requests if r.url.path == "/v3.0/tracks/"]


def test_more_tracks_leaves_out_what_the_show_played(fake_jamendo: FakeJamendo) -> None:
    played = {"jamendo-1001", "jamendo-1003"}

    tracks = fake_jamendo.source.more_tracks("jazz", exclude=played)

    assert [track.id for track in tracks] == ["jamendo-1002", "jamendo-1004"]
    # The first page had something new: one search, asked like any first page.
    (params,) = _tracks_requests(fake_jamendo)
    assert "offset" not in params
    assert params["limit"] == "50"


def test_more_tracks_reads_the_next_page_when_a_page_brings_nothing_new(
    fake_jamendo: FakeJamendo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jamendo, "SEARCH_LIMIT", 2)  # two pages of two tracks
    played = {"jamendo-1001", "jamendo-1002"}

    tracks = fake_jamendo.source.more_tracks("jazz", exclude=played)

    assert [track.id for track in tracks] == ["jamendo-1003", "jamendo-1004"]
    assert [params.get("offset") for params in _tracks_requests(fake_jamendo)] == [None, "2"]


def test_more_tracks_gives_nothing_once_every_track_was_played(fake_jamendo: FakeJamendo) -> None:
    assert fake_jamendo.source.more_tracks("jazz", exclude=ALL_IDS) == []
    # The reply says the query has four results: the first page is the last one, so no
    # second page is asked.
    (params,) = _tracks_requests(fake_jamendo)
    assert "offset" not in params
    assert params["fullcount"] == "true"


def test_more_tracks_ends_a_query_at_an_exact_multiple_of_the_page_size(
    fake_jamendo: FakeJamendo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two full pages of two played tracks. The total says there is no third page, so
    none is asked for (D044)."""
    monkeypatch.setattr(jamendo, "SEARCH_LIMIT", 2)

    assert fake_jamendo.source.more_tracks("jazz", exclude=ALL_IDS) == []

    assert [params.get("offset") for params in _tracks_requests(fake_jamendo)] == [None, "2"]


def test_more_tracks_ends_a_query_on_a_page_that_is_not_full(
    fake_jamendo: FakeJamendo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jamendo, "SEARCH_LIMIT", 3)  # a page of three, then a page of one

    assert fake_jamendo.source.more_tracks("jazz", exclude=ALL_IDS) == []

    assert [params.get("offset") for params in _tracks_requests(fake_jamendo)] == [None, "3"]


def test_a_page_that_lost_a_track_to_the_license_filter_does_not_end_the_query(
    fake_jamendo: FakeJamendo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A track with no Creative Commons license is left out, so a full page can hold fewer
    tracks than the page size. The end of a query is read from the total, not from that
    number (D044): the next page is asked."""
    monkeypatch.setattr(jamendo, "SEARCH_LIMIT", 2)
    fake_jamendo._tracks["results"][0]["license_ccurl"] = ""  # jamendo-1001

    tracks = fake_jamendo.source.more_tracks("jazz", exclude={"jamendo-1002"})

    assert [track.id for track in tracks] == ["jamendo-1003", "jamendo-1004"]
    assert [params.get("offset") for params in _tracks_requests(fake_jamendo)] == [None, "2"]


def test_a_page_past_the_end_of_a_query_is_a_true_end_not_a_false_empty(
    fake_jamendo: FakeJamendo,
) -> None:
    """Jamendo answers no results and the total: nothing is asked again, nothing fails."""
    tracks, last = fake_jamendo.source._page(Query(tag="jazz"), "jazz", offset=50)

    assert (tracks, last) == ([], True)
    assert len(_tracks_requests(fake_jamendo)) == 1


def test_more_tracks_stops_after_ten_pages_of_a_query(fake_jamendo: FakeJamendo) -> None:
    """A search that keeps answering played tracks must not hold a step for ever."""
    fake_jamendo.ignore_offset = True

    assert fake_jamendo.source.more_tracks("jazz", exclude=ALL_IDS) == []

    offsets = [params.get("offset") for params in _tracks_requests(fake_jamendo)]
    assert offsets == [None, *(str(page * 50) for page in range(1, 10))]


def test_more_tracks_relaxes_the_wish_then_the_speed(fake_jamendo: FakeJamendo) -> None:
    """The same steps as find_tracks: every filter, no wish filters, no speed. A query
    that matches nothing says so with a total of zero, so it is asked once."""
    fake_jamendo.reject_params = {"fuzzytags", "speed"}
    filters = WishFilters(tags=["piano"])

    tracks = fake_jamendo.source.more_tracks(
        "jazz", energy="upbeat", filters=filters, exclude={"jamendo-1001"}
    )

    assert [track.id for track in tracks] == ["jamendo-1002", "jamendo-1003", "jamendo-1004"]
    used = [sorted(set(p) & {"fuzzytags", "speed"}) for p in _tracks_requests(fake_jamendo)]
    assert used == [["fuzzytags", "speed"], ["speed"], []]


# False empty replies (D044): Jamendo now and then answers no results for a query that
# has thousands, in runs. Such a reply carries no total, which is how it is told from the
# end of a query.


def test_more_tracks_asks_again_after_a_run_of_false_empty_replies(
    fake_jamendo: FakeJamendo,
) -> None:
    fake_jamendo.empty_replies = jamendo.FALSE_EMPTY_RETRIES  # the last try is answered

    tracks = fake_jamendo.source.more_tracks("jazz", exclude={"jamendo-1001"})

    assert [track.id for track in tracks] == ["jamendo-1002", "jamendo-1003", "jamendo-1004"]
    assert len(_tracks_requests(fake_jamendo)) == jamendo.FALSE_EMPTY_RETRIES + 1


def test_more_tracks_fails_when_jamendo_keeps_answering_nothing(fake_jamendo: FakeJamendo) -> None:
    """Never an empty list: that would read as "the style has nothing left"."""
    fake_jamendo.empty_replies = 99

    with pytest.raises(JamendoError, match="Jamendo did not answer the search"):
        fake_jamendo.source.more_tracks("jazz", exclude=set())

    assert len(_tracks_requests(fake_jamendo)) == jamendo.FALSE_EMPTY_RETRIES + 1


def test_false_empty_replies_never_relax_the_query(fake_jamendo: FakeJamendo) -> None:
    """With the wish filters and the energy on, a false empty reply must not make the
    step fall back to a wider query: the listener would lose the wish without a word."""
    filters = WishFilters(tags=["piano"])
    fake_jamendo.empty_replies = 3

    tracks = fake_jamendo.source.more_tracks("jazz", energy="calm", filters=filters, exclude=set())

    assert len(tracks) == 4
    requests = _tracks_requests(fake_jamendo)
    assert len(requests) == 4
    assert all(p["fuzzytags"] == "piano" and p["speed"] == "verylow low" for p in requests)

    # A run that outlasts the tries fails the search, still without a wider query.
    fake_jamendo.requests.clear()
    fake_jamendo.empty_replies = 99

    with pytest.raises(JamendoError, match="Jamendo did not answer the search"):
        fake_jamendo.source.more_tracks("jazz", energy="calm", filters=filters, exclude=set())

    requests = _tracks_requests(fake_jamendo)
    assert len(requests) == jamendo.FALSE_EMPTY_RETRIES + 1
    assert all(p["fuzzytags"] == "piano" and p["speed"] == "verylow low" for p in requests)


def test_a_false_empty_reply_is_one_with_no_results_and_no_total() -> None:
    no_total = {"headers": {"status": "success", "results_count": 0}, "results": []}
    no_match = {"headers": {"results_count": 0, "results_fullcount": "0"}, "results": []}
    past_the_end = {"headers": {"results_count": 0, "results_fullcount": "414"}, "results": []}

    assert jamendo._is_false_empty(no_total) is True
    assert jamendo._is_false_empty(no_match) is False
    assert jamendo._is_false_empty(past_the_end) is False
    assert jamendo._is_last_page(no_match, offset=0) is True
    assert jamendo._is_last_page(past_the_end, offset=450) is True


def test_the_last_page_is_read_from_the_total() -> None:
    """The figures of the live probe: 414 results, pages of fifty."""
    middle = {"headers": {"results_fullcount": "414"}, "results": [{}] * 50}
    final = {"headers": {"results_fullcount": "414"}, "results": [{}] * 14}

    assert jamendo._is_last_page(middle, offset=300) is False
    assert jamendo._is_last_page(middle, offset=350) is False
    assert jamendo._is_last_page(final, offset=400) is True
    # Results with no total were not seen on the live API. Then a full page means "ask
    # the next one", and the reply is used, not taken for a false empty one.
    assert jamendo._is_last_page({"headers": {}, "results": [{}] * 50}, offset=0) is False
    assert jamendo._is_last_page({"headers": {}, "results": [{}] * 49}, offset=0) is True
    assert jamendo._is_false_empty({"headers": {}, "results": [{}]}) is False


def test_more_tracks_keeps_the_strictest_query_that_has_something_new(
    fake_jamendo: FakeJamendo,
) -> None:
    filters = WishFilters(tags=["piano"])

    fake_jamendo.source.more_tracks("jazz", energy="calm", filters=filters, exclude=set())

    (params,) = _tracks_requests(fake_jamendo)
    assert params["fuzzytags"] == "piano"
    assert params["speed"] == "verylow low"


def test_more_tracks_rejects_an_unknown_style(fake_jamendo: FakeJamendo) -> None:
    with pytest.raises(JamendoError, match="polka"):
        fake_jamendo.source.more_tracks("polka", exclude=set())


def test_find_tracks_never_sends_an_offset(fake_jamendo: FakeJamendo) -> None:
    """A show of a set length searches exactly as before the endless show (D044)."""
    fake_jamendo.source.find_tracks("jazz", energy="calm", minimum=3)
    fake_jamendo.source.list_tracks("jazz")

    assert all("offset" not in params for params in _tracks_requests(fake_jamendo))


def test_find_tracks_keeps_its_own_way_with_empty_replies(fake_jamendo: FakeJamendo) -> None:
    """The search of a show of a set length is not changed by the endless show (D044):
    no `fullcount`, three tries, then the next relaxed query, and no error."""
    fake_jamendo.empty_replies = 3

    tracks = fake_jamendo.source.find_tracks("jazz", energy="calm", minimum=3)

    assert len(tracks) == 4
    requests = _tracks_requests(fake_jamendo)
    assert all("fullcount" not in params for params in requests)
    assert ["speed" in params for params in requests] == [True, True, True, False]


def test_source_attributes() -> None:
    assert JamendoSource.delivery == "file"
    assert JamendoSource.accepts_wish is True
    assert JamendoSource.needs_facts_lookup is True
    assert JamendoSource.endless is True  # D042


def test_find_tracks_rejects_an_unknown_style(fake_jamendo: FakeJamendo) -> None:
    with pytest.raises(JamendoError, match="polka"):
        fake_jamendo.source.find_tracks("polka", minimum=1)


def test_audio_path_fetches_once_then_reads_the_cache(
    fake_jamendo: FakeJamendo, tmp_path: Path
) -> None:
    track = fake_jamendo.source.list_tracks("jazz")[0]

    path = fake_jamendo.source.audio_path(track)

    assert path == tmp_path / "cache" / "jamendo" / "jamendo-1001.mp3"
    assert path.exists()
    assert not path.with_suffix(".part").exists()
    downloads = [r for r in fake_jamendo.requests if r.url.host == "prod-1.storage.jamendo.com"]
    assert len(downloads) == 1
    assert downloads[0].url.params["trackid"] == "1001"

    assert fake_jamendo.source.audio_path(track) == path
    assert len([r for r in fake_jamendo.requests if r.url.host.startswith("prod-")]) == 1


def test_get_track_fetches_by_id_and_derives_the_style(fake_jamendo: FakeJamendo) -> None:
    track = fake_jamendo.source.get_track("jamendo-1003")

    assert track.title == "Montreal Streets"
    assert track.style == "jazz"
    assert _params(fake_jamendo.requests[0])["id"] == "1003"
    # Known now: no second call.
    assert fake_jamendo.source.get_track("jamendo-1003") is track
    assert len(fake_jamendo.requests) == 2


def test_get_track_unknown_ids(fake_jamendo: FakeJamendo) -> None:
    with pytest.raises(UnknownTrackError):
        fake_jamendo.source.get_track("jazz-01")
    with pytest.raises(UnknownTrackError):
        fake_jamendo.source.get_track("jamendo-9999")


def test_audio_path_for_a_track_seen_in_another_process(fake_jamendo: FakeJamendo) -> None:
    """The API restarts between the search and the download: the stream URL is fetched again."""
    track = fake_jamendo.source.list_tracks("jazz")[2]
    fresh = JamendoSource(
        "test-client-id", fake_jamendo.source._dir.parent, http=fake_jamendo.source._http
    )

    path = fresh.audio_path(track)

    assert path.exists()


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        (5, "Invalid Client Id", "JAMENDO_CLIENT_ID"),
        (6, "Rate Limit Exceeded", "rate limit"),
        (11, "Suspended Application", "suspended"),
        (3, "Type", "Jamendo error 3: Type"),
    ],
)
def test_api_errors_become_readable_messages(
    fake_jamendo: FakeJamendo, code: int, message: str, expected: str
) -> None:
    fake_jamendo.api_error = (code, message)

    with pytest.raises(JamendoError, match=expected):
        fake_jamendo.source.list_tracks("jazz")


def test_network_errors_become_jamendo_errors(tmp_path: Path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    source = JamendoSource(
        "id", tmp_path, http=httpx.Client(transport=httpx.MockTransport(explode))
    )

    with pytest.raises(JamendoError, match="unreachable"):
        source.list_tracks("jazz")


def test_http_errors_become_jamendo_errors(tmp_path: Path) -> None:
    def teapot(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    source = JamendoSource("id", tmp_path, http=httpx.Client(transport=httpx.MockTransport(teapot)))

    with pytest.raises(JamendoError, match="HTTP 503"):
        source.list_tracks("jazz")
