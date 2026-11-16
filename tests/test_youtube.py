"""The YouTube source on recorded replies (D038). No test here reaches the network."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from radio_llama.music import (
    MusicSourceError,
    SongProposal,
    UnknownTrackError,
    YouTubeError,
    YouTubeQuotaError,
    YouTubeSource,
)
from radio_llama.music.youtube import (
    CacheEntry,
    Video,
    cache_key,
    is_official,
    parse_duration,
    read_cache,
    reject_reason,
)
from radio_llama.trace import tracing
from tests.conftest import YOUTUBE_FIXTURES, FakeYouTube

BLUE_WINDOW = SongProposal(artist="Example Artist", title="Blue Window")
MONTREAL = SongProposal(artist="Lea Example", title="Montreal Streets")
NIGHT_TRAIN = SongProposal(artist="We Are FM", title="Night Train")
RED_LIGHTS = SongProposal(artist="Example Records", title="Red Lights")
SLOW_RIVER = SongProposal(artist="River Uploads", title="Slow River")
PROPOSALS = [BLUE_WINDOW, MONTREAL, NIGHT_TRAIN, RED_LIGHTS, SLOW_RIVER]


def _params(request: httpx.Request) -> dict[str, str]:
    return dict(request.url.params)


def _videos_by_id() -> dict[str, dict]:
    body = json.loads((YOUTUBE_FIXTURES / "videos.json").read_text(encoding="utf-8"))
    return {item["id"]: item for item in body["items"]}


def test_youtube_offers_no_styles(fake_youtube: FakeYouTube) -> None:
    """The wish is the theme of a YouTube show (D039)."""
    assert fake_youtube.source.list_styles() == []
    assert fake_youtube.requests == []


def test_find_tracks_without_a_style_leaves_it_blank(fake_youtube: FakeYouTube) -> None:
    (track,) = fake_youtube.source.find_tracks([BLUE_WINDOW], style=None, minimum=1)

    assert track.style == ""  # Track needs a str; "" like album (D039)
    assert track.title == "Blue Window"


def test_list_tracks_is_empty_without_a_call(fake_youtube: FakeYouTube) -> None:
    assert fake_youtube.source.list_tracks("jazz") == []
    assert fake_youtube.requests == []


def test_find_tracks_maps_a_proposal_to_its_video(fake_youtube: FakeYouTube) -> None:
    (track,) = fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    assert track.id == "youtube-okTopic0001"
    assert track.video_id == "okTopic0001"
    assert track.title == "Blue Window"  # the proposal's, not YouTube's
    assert track.artist == "Example Artist"
    assert track.style == "jazz"
    assert track.energy is None
    assert track.album == ""
    assert track.year is None
    assert track.duration_s == 214.0
    assert track.license == "YouTube embed"
    assert track.license_url == "https://www.youtube.com/t/terms"
    assert track.track_url == "https://www.youtube.com/watch?v=okTopic0001"
    assert track.artist_url == "https://www.youtube.com/channel/UCtopic000001"
    assert track.audio_file is None
    assert track.tags == []
    assert track.facts == []


def test_find_tracks_sends_one_search_then_one_videos_call(fake_youtube: FakeYouTube) -> None:
    fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    search, videos = fake_youtube.requests
    assert search.url.host == "www.googleapis.com"
    assert search.url.path == "/youtube/v3/search"
    params = _params(search)
    assert params["q"] == "Example Artist Blue Window"
    assert params["part"] == "snippet"
    assert params["type"] == "video"
    assert params["videoCategoryId"] == "10"
    assert params["videoEmbeddable"] == "true"
    assert params["videoSyndicated"] == "true"
    assert params["regionCode"] == "FR"
    assert params["maxResults"] == "3"
    assert params["key"] == "test-api-key"
    assert videos.url.path == "/youtube/v3/videos"
    params = _params(videos)
    assert params["part"] == "snippet,contentDetails,status"
    assert params["id"].split(",") == [
        "okPlain0001",
        "noEmbed0001",
        "liveNow0001",
        "tooLong0001",
        "ageRestr001",
        "blockedFR01",
        "okTopic0001",
    ]
    assert params["key"] == "test-api-key"


@pytest.mark.parametrize(
    ("video_id", "reason"),
    [
        ("okPlain0001", None),
        ("okTopic0001", None),
        ("noEmbed0001", "not embeddable"),
        ("liveNow0001", "not processed"),
        ("tooLong0001", "lasts 902 s"),
        ("ageRestr001", "age restricted"),
        ("blockedFR01", "blocked in FR"),
    ],
)
def test_reject_reason_on_the_recorded_videos(video_id: str, reason: str | None) -> None:
    assert reject_reason(_videos_by_id()[video_id], "FR") == reason


def test_reject_reason_on_edited_videos() -> None:
    good = _videos_by_id()["okTopic0001"]

    assert reject_reason({**good, "status": {**good["status"], "privacyStatus": "unlisted"}}, "FR")
    assert reject_reason({**good, "status": {**good["status"], "uploadStatus": "uploaded"}}, "FR")
    live = {**good, "snippet": {**good["snippet"], "liveBroadcastContent": "upcoming"}}
    assert reject_reason(live, "FR") == "live or upcoming"
    short = {**good, "contentDetails": {**good["contentDetails"], "duration": "PT59S"}}
    assert reject_reason(short, "FR") == "lasts 59 s"
    unreadable = {**good, "contentDetails": {**good["contentDetails"], "duration": "soon"}}
    assert reject_reason(unreadable, "FR") == "no duration"
    allowed = {
        **good,
        "contentDetails": {**good["contentDetails"], "regionRestriction": {"allowed": ["US"]}},
    }
    assert reject_reason(allowed, "FR") == "not allowed in FR"
    assert reject_reason(allowed, "US") is None
    assert reject_reason(_videos_by_id()["blockedFR01"], "US") is None
    assert reject_reason(good, "FR") is None


def test_the_region_setting_is_the_one_checked(tmp_path: Path) -> None:
    fake = FakeYouTube(tmp_path / "cache", region="US")

    (track,) = fake.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    assert _params(fake.searches()[0])["regionCode"] == "US"
    assert track.video_id == "okTopic0001"  # still the artist's channel first


def test_the_artist_channel_wins_over_an_earlier_upload(fake_youtube: FakeYouTube) -> None:
    tracks = fake_youtube.source.find_tracks(
        [BLUE_WINDOW, NIGHT_TRAIN, MONTREAL], style="rock", minimum=3
    )

    # Blue Window: a plain upload comes first, the "- Topic" channel is preferred.
    # Night Train: a plain upload first, the VEVO channel is preferred.
    # Montreal Streets: no artist channel among the kept videos, the first one is taken.
    assert [track.video_id for track in tracks] == ["okTopic0001", "nightTrn001", "montreal001"]


@pytest.mark.parametrize(
    ("channel", "official"),
    [
        ("Example Artist - Topic", True),
        ("WeAreFMVEVO", True),
        ("Example Records Official", True),
        ("official lea", True),
        ("Example Official Videos", True),
        ("Unofficial Fan Club", False),
        ("Officially Nobody", False),
        ("Topic of the day", False),
        ("Some Uploader", False),
    ],
)
def test_is_official(channel: str, official: bool) -> None:
    assert is_official(channel) is official


def test_a_proposal_without_a_playable_video_is_dropped(fake_youtube: FakeYouTube) -> None:
    broken = SongProposal(artist="Nobody", title="Broken Song")
    unknown = SongProposal(artist="Nobody", title="Never Recorded")

    tracks = fake_youtube.source.find_tracks([broken, unknown, MONTREAL], style="jazz", minimum=3)

    assert [track.video_id for track in tracks] == ["montreal001"]
    # The unknown song's search returned nothing, so no videos call for it.
    assert [request.url.path.rsplit("/", 1)[1] for request in fake_youtube.requests] == [
        "search",
        "videos",
        "search",
        "search",
        "videos",
    ]


def test_find_tracks_stops_once_minimum_is_reached(fake_youtube: FakeYouTube) -> None:
    tracks = fake_youtube.source.find_tracks(PROPOSALS, style="pop", minimum=3)

    assert len(tracks) == 3
    assert len(fake_youtube.searches()) == 3  # the two spare proposals cost nothing


def test_the_same_video_for_two_proposals_is_kept_once(fake_youtube: FakeYouTube) -> None:
    river_again = SongProposal(artist="Someone Else", title="Slow River")

    tracks = fake_youtube.source.find_tracks([SLOW_RIVER, river_again], style="pop", minimum=2)

    assert [track.video_id for track in tracks] == ["slowRivr001"]


def test_cache_hit_avoids_a_second_search(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    first = fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)
    again = fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)
    # A fresh process on the same cache folder reads the file too.
    fresh = FakeYouTube(tmp_path / "cache")
    later = fresh.source.find_tracks(
        [SongProposal(artist="example  artist", title="BLUE WINDOW")], style="jazz", minimum=1
    )

    assert len(fake_youtube.searches()) == 1
    assert fresh.requests == []
    assert first == again
    assert later[0].video_id == "okTopic0001"
    (cache_file,) = (tmp_path / "cache" / "youtube").iterdir()
    assert cache_file.name == f"{cache_key(BLUE_WINDOW)}.json"
    entry = CacheEntry.model_validate_json(cache_file.read_text(encoding="utf-8"))
    assert entry.artist == "Example Artist"
    assert entry.title == "Blue Window"
    assert entry.fetched_at.tzinfo is not None
    assert entry.video == Video(
        id="okTopic0001",
        title="Blue Window",
        channel_id="UCtopic000001",
        channel_title="Example Artist - Topic",
        duration_s=214.0,
    )


def test_a_miss_is_cached_too(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    unknown = SongProposal(artist="Nobody", title="Never Recorded")

    assert fake_youtube.source.find_tracks([unknown], style="jazz", minimum=1) == []
    assert fake_youtube.source.find_tracks([unknown], style="jazz", minimum=1) == []

    assert len(fake_youtube.searches()) == 1
    (cache_file,) = (tmp_path / "cache" / "youtube").iterdir()
    assert CacheEntry.model_validate_json(cache_file.read_text(encoding="utf-8")).video is None


def test_a_cache_entry_older_than_30_days_is_refreshed(
    fake_youtube: FakeYouTube, tmp_path: Path
) -> None:
    folder = tmp_path / "cache" / "youtube"
    folder.mkdir(parents=True)
    old = CacheEntry(
        artist="Example Artist",
        title="Blue Window",
        fetched_at=datetime.now(UTC) - timedelta(days=31),
        video=Video(
            id="goneVideo01", title="x", channel_id="c", channel_title="c", duration_s=200.0
        ),
    )
    path = folder / f"{cache_key(BLUE_WINDOW)}.json"
    path.write_text(old.model_dump_json(), encoding="utf-8")

    (track,) = fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    assert track.video_id == "okTopic0001"
    assert len(fake_youtube.searches()) == 1
    refreshed = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
    assert refreshed.fetched_at > old.fetched_at


def test_an_expired_cache_entry_is_deleted(tmp_path: Path) -> None:
    """API data never stays on disk past the 30 days, even when no refresh follows."""
    expired = tmp_path / "expired.json"
    fresh = tmp_path / "fresh.json"
    video = Video(id="v", title="t", channel_id="c", channel_title="c", duration_s=200.0)
    for path, age in ((expired, timedelta(days=31)), (fresh, timedelta(days=29))):
        entry = CacheEntry(artist="a", title="t", fetched_at=datetime.now(UTC) - age, video=video)
        path.write_text(entry.model_dump_json(), encoding="utf-8")

    assert read_cache(expired) is None
    assert not expired.exists()
    assert read_cache(fresh) is not None
    assert fresh.exists()


def test_a_naive_fetched_at_is_a_broken_entry(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    """A timestamp without a zone cannot be aged against the clock: the entry is refreshed."""
    folder = tmp_path / "cache" / "youtube"
    folder.mkdir(parents=True)
    path = folder / f"{cache_key(BLUE_WINDOW)}.json"
    naive = {"artist": "Example Artist", "title": "Blue Window", "video": None}
    naive["fetched_at"] = "2026-11-01T12:00:00"
    path.write_text(json.dumps(naive), encoding="utf-8")

    (track,) = fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    assert track.video_id == "okTopic0001"
    assert len(fake_youtube.searches()) == 1
    refreshed = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
    assert refreshed.fetched_at.tzinfo is not None


def test_a_broken_cache_file_is_replaced(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    folder = tmp_path / "cache" / "youtube"
    folder.mkdir(parents=True)
    path = folder / f"{cache_key(BLUE_WINDOW)}.json"
    path.write_text("not json", encoding="utf-8")

    (track,) = fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    assert track.video_id == "okTopic0001"
    assert CacheEntry.model_validate_json(path.read_text(encoding="utf-8")).video is not None


def test_quota_exceeded_is_a_clear_error(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    fake_youtube.quota_exceeded = True

    with pytest.raises(YouTubeQuotaError, match="100 searches a day"):
        fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)
    assert not (tmp_path / "cache" / "youtube").exists()  # a failure is not cached


def test_a_bad_key_names_the_variable(tmp_path: Path) -> None:
    def reject(request: httpx.Request) -> httpx.Response:
        body = {
            "error": {
                "code": 400,
                "message": "API key not valid. Please pass a valid API key.",
                "errors": [{"reason": "badRequest", "domain": "global"}],
                "status": "INVALID_ARGUMENT",
                "details": [{"reason": "API_KEY_INVALID", "domain": "googleapis.com"}],
            }
        }
        return httpx.Response(400, json=body)

    source = YouTubeSource(
        "bad", tmp_path, "FR", http=httpx.Client(transport=httpx.MockTransport(reject))
    )

    with pytest.raises(YouTubeError, match="YOUTUBE_API_KEY"):
        source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)


def test_network_and_http_errors_become_youtube_errors(tmp_path: Path) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    def teapot(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": 503, "message": "Backend Error"}})

    down = YouTubeSource(
        "k", tmp_path, "FR", http=httpx.Client(transport=httpx.MockTransport(explode))
    )
    busy = YouTubeSource(
        "k", tmp_path, "FR", http=httpx.Client(transport=httpx.MockTransport(teapot))
    )

    with pytest.raises(YouTubeError, match="unreachable"):
        down.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)
    with pytest.raises(YouTubeError, match="HTTP 503.*Backend Error"):
        busy.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("PT3M33S", 213.0),
        ("PT1H2M3S", 3723.0),
        ("PT45S", 45.0),
        ("PT5M", 300.0),
        ("PT1H", 3600.0),
        ("P1DT1S", 86401.0),
        ("P0D", 0.0),
        ("PT", 0.0),
        ("", None),
        ("3:33", None),
        ("PT3.5M", None),
    ],
)
def test_parse_duration(text: str, seconds: float | None) -> None:
    assert parse_duration(text) == seconds


def test_the_key_never_reaches_the_trace(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    with tracing(tmp_path / "trace"):
        fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)
        fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    text = (tmp_path / "trace" / "youtube.jsonl").read_text(encoding="utf-8")
    assert "test-api-key" not in text
    assert '"key"' not in text
    search, videos, cached = [json.loads(line) for line in text.splitlines()]
    assert search["method"] == "search"
    assert search["params"]["q"] == "Example Artist Blue Window"
    assert search["status"] == 200
    assert search["body"]["items"][0]["id"]["videoId"] == "okPlain0001"
    assert search["error"] is None
    assert videos["method"] == "videos"
    assert videos["body"]["items"][-1]["id"] == "okTopic0001"
    assert cached["method"] == "cache"
    assert cached["video"]["id"] == "okTopic0001"


def test_quota_error_is_traced(fake_youtube: FakeYouTube, tmp_path: Path) -> None:
    fake_youtube.quota_exceeded = True
    with tracing(tmp_path / "trace"), pytest.raises(YouTubeQuotaError):
        fake_youtube.source.find_tracks([BLUE_WINDOW], style="jazz", minimum=1)

    (record,) = [
        json.loads(line)
        for line in (tmp_path / "trace" / "youtube.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert record["status"] == 403
    assert "100 searches a day" in record["error"]


def test_get_track_knows_found_tracks_only(fake_youtube: FakeYouTube) -> None:
    (track,) = fake_youtube.source.find_tracks([MONTREAL], style="jazz", minimum=1)

    assert fake_youtube.source.get_track("youtube-montreal001") is track
    with pytest.raises(UnknownTrackError):
        fake_youtube.source.get_track("youtube-okTopic0001")
    with pytest.raises(UnknownTrackError):
        fake_youtube.source.get_track("jazz-01")


def test_get_track_forgets_the_previous_result(fake_youtube: FakeYouTube) -> None:
    """The source keeps the last result only, so a long-lived server does not pile tracks up."""
    fake_youtube.source.find_tracks([MONTREAL], style="jazz", minimum=1)
    (track,) = fake_youtube.source.find_tracks([RED_LIGHTS], style="jazz", minimum=1)

    assert fake_youtube.source.get_track("youtube-redLight001") is track
    with pytest.raises(UnknownTrackError):
        fake_youtube.source.get_track("youtube-montreal001")


def test_audio_path_is_an_error(fake_youtube: FakeYouTube) -> None:
    (track,) = fake_youtube.source.find_tracks([MONTREAL], style="jazz", minimum=1)

    with pytest.raises(MusicSourceError, match="no audio file"):
        fake_youtube.source.audio_path(track)


def test_source_attributes() -> None:
    assert YouTubeSource.delivery == "embed"
    assert YouTubeSource.accepts_wish is True
    assert YouTubeSource.needs_facts_lookup is True
