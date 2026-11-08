"""The Jamendo source on recorded replies (D030). No test here reaches the network."""

import shutil
from pathlib import Path

import httpx
import pytest

from radio_llama.music import JamendoError, JamendoSource, UnknownTrackError, WishFilters
from radio_llama.music.jamendo import STYLE_TAGS, description_facts, parse_license
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
    assert [sorted(set(p) & {"fuzzytags", "speed"}) for p in tracks_params] == [
        ["fuzzytags", "speed"],
        ["speed"],
        [],
    ]


def test_find_tracks_stops_relaxing_once_enough(fake_jamendo: FakeJamendo) -> None:
    tracks = fake_jamendo.source.find_tracks("jazz", energy="upbeat", minimum=3)

    assert len(tracks) == 4
    assert len([r for r in fake_jamendo.requests if r.url.path == "/v3.0/tracks/"]) == 1


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
