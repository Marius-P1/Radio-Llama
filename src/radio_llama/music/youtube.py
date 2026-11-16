"""MusicSource on the YouTube Data API v3 (D029, D038).

YouTube is the one free route to famous songs. The LLM proposes songs (artist and title,
see `script/playlist.py`), and this source checks each proposal against YouTube: one
`search.list` for the proposal, then one `videos.list` on the ids it returned, keeping a
video only when the embedded player can play it in full (public, processed, embeddable,
not live, not age restricted, not blocked in the region, between one and twelve minutes).
Among the kept videos the artist's own channel wins ("<Artist> - Topic", "VEVO" or the
word "Official" in the channel name), else the first result.

Nothing is downloaded: a YouTube track has no audio file, only a `video_id` that the
player streams through the official embedded player. The search quota is small (100
searches a day per project since June 2026), so each proposal's answer, a miss included,
is cached in `<cache_dir>/youtube/<hash>.json` for 30 days, the longest the Developer
Policies allow API data to be kept. Google has a Python client, but the two read calls are
plain HTTPS GETs with JSON, so they go through httpx like Jamendo's.
"""

import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from radio_llama import trace
from radio_llama.models import Track
from radio_llama.music.base import MusicSourceError, UnknownTrackError

log = logging.getLogger(__name__)

API_URL = "https://www.googleapis.com/youtube/v3"
ID_PREFIX = "youtube-"
TIMEOUT_S = 30.0
SEARCH_RESULTS = 3  # candidates checked per proposal
MUSIC_CATEGORY = "10"  # YouTube's "Music" video category
# A song lasts between one and twelve minutes: shorter is a teaser, longer is a mix.
MIN_DURATION_S = 60.0
MAX_DURATION_S = 12 * 60.0
CACHE_DAYS = 30  # the Developer Policies cap stored API data at 30 days
SEARCHES_PER_DAY = 100
LICENSE = "YouTube embed"
LICENSE_URL = "https://www.youtube.com/t/terms"
WATCH_URL = "https://www.youtube.com/watch?v="
CHANNEL_URL = "https://www.youtube.com/channel/"
# Channel names that mark an artist's own upload: auto-generated topic channels, VEVO,
# and the usual "Official" suffix. "Official" counts as a whole word: "Unofficial" does not.
TOPIC_SUFFIX = " - Topic"
VEVO = "vevo"
_OFFICIAL = re.compile(r"\bofficial\b")

# ISO 8601 duration as YouTube writes it: PT1H2M3S, PT3M33S, PT45S, or P0D for a live stream.
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


class YouTubeError(MusicSourceError):
    """A YouTube call failed. The message names the cause."""


class YouTubeQuotaError(YouTubeError):
    """The daily search quota is used up. Tomorrow's shows will work again."""


class SongProposal(BaseModel):
    """One song the LLM proposes for the show: the search query and the credits (D038)."""

    artist: str = Field(min_length=1)
    title: str = Field(min_length=1)

    def query(self) -> str:
        return f"{self.artist} {self.title}"


class Video(BaseModel):
    """What the cache keeps of a chosen video: enough to build the Track again."""

    id: str
    title: str  # YouTube's title, kept for reading the cache; the Track uses the proposal's
    channel_id: str
    channel_title: str
    duration_s: float


class CacheEntry(BaseModel):
    """`<cache_dir>/youtube/<hash>.json`: the proposal, when it was looked up, its video."""

    artist: str
    title: str
    fetched_at: AwareDatetime  # a naive time cannot be aged, so it fails validation
    video: Video | None  # None: YouTube has no playable video for this song


class YouTubeSource:
    delivery = "embed"
    accepts_wish = True
    needs_facts_lookup = True

    def __init__(
        self, api_key: str, cache_dir: Path, region: str, http: httpx.Client | None = None
    ) -> None:
        self._api_key = api_key
        self._dir = cache_dir / "youtube"
        self._region = region
        self._http = http if http is not None else httpx.Client(timeout=TIMEOUT_S)
        self._tracks: dict[str, Track] = {}  # the last find_tracks result, for get_track

    def list_styles(self) -> list[str]:
        """Always empty: a YouTube show has no style, the listener's wish is its theme (D039)."""
        return []

    def list_tracks(self, style: str) -> list[Track]:
        """Always empty: YouTube's own search by genre returns mixes, not songs. The songs
        come from the LLM's proposals through `find_tracks` (D038)."""
        return []

    def find_tracks(
        self, proposals: list[SongProposal], *, style: str | None, minimum: int
    ) -> list[Track]:
        """One track per proposal that has a playable video, in the proposals' order.

        Proposals are looked up until `minimum` tracks are found, so the spare proposals
        the writer adds cost no search unless an earlier one has no video. A video found
        for two proposals is kept once. `get_track` then knows this result only: the
        pipeline uses the tracks of its own run and the dict must not grow with the server.
        """
        found: list[Track] = []
        seen: set[str] = set()
        for proposal in proposals:
            if len(found) >= minimum:
                break
            video = self._lookup(proposal)
            if video is None or video.id in seen:
                continue
            seen.add(video.id)
            found.append(to_track(proposal, video, style))
        self._tracks = {track.id: track for track in found}
        return found

    def get_track(self, track_id: str) -> Track:
        """A track of the last `find_tracks` call. YouTube cannot tell the song from the video."""
        try:
            return self._tracks[track_id]
        except KeyError:
            raise UnknownTrackError(f"No track with id {track_id!r}") from None

    def audio_path(self, track: Track) -> Path:
        raise MusicSourceError(
            f"Track {track.id!r} plays in the embedded YouTube player; it has no audio file."
        )

    # Internals

    def _lookup(self, proposal: SongProposal) -> Video | None:
        """The chosen video for a proposal, from the cache when it is under 30 days old."""
        path = self._dir / f"{cache_key(proposal)}.json"
        cached = read_cache(path)
        started = time.perf_counter()
        if cached is not None:
            trace.record(
                "youtube",
                started,
                method="cache",
                artist=proposal.artist,
                title=proposal.title,
                video=cached.video.model_dump() if cached.video else None,
            )
            return cached.video
        video = self._search_video(proposal)
        entry = CacheEntry(
            artist=proposal.artist, title=proposal.title, fetched_at=datetime.now(UTC), video=video
        )
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")
        return video

    def _search_video(self, proposal: SongProposal) -> Video | None:
        """One search, then one videos call on its ids. None when no result is playable."""
        search = self._get(
            "search",
            {
                "part": "snippet",
                "q": proposal.query(),
                "type": "video",
                "videoCategoryId": MUSIC_CATEGORY,
                "videoEmbeddable": "true",
                "videoSyndicated": "true",
                "regionCode": self._region,
                "maxResults": str(SEARCH_RESULTS),
            },
        )
        ids = [item["id"]["videoId"] for item in search if "videoId" in (item.get("id") or {})]
        if not ids:
            log.info("No YouTube result for %s", proposal.query())
            return None
        videos = self._get("videos", {"part": "snippet,contentDetails,status", "id": ",".join(ids)})
        kept = []
        for raw in videos:
            reason = reject_reason(raw, self._region)
            if reason is None:
                kept.append(to_video(raw))
            else:
                log.info("Skipping YouTube video %s for %s: %s", raw.get("id"), proposal, reason)
        if not kept:
            return None
        return next((video for video in kept if is_official(video.channel_title)), kept[0])

    def _get(self, method: str, params: dict[str, str]) -> list[dict]:
        """One API call, recorded in `youtube.jsonl` without the key (D037). Its items."""
        url = f"{API_URL}/{method}"
        sent = {"method": method, "params": params, "redact": self._api_key}
        started = time.perf_counter()
        try:
            response = self._http.get(url, params={**params, "key": self._api_key})
        except httpx.HTTPError as exc:
            trace.record("youtube", started, error=str(exc), **sent, status=None, body=None)
            raise YouTubeError(f"YouTube is unreachable: {exc}") from exc
        status = response.status_code
        body = trace.response_body(response)
        error = reply_error(method, status, body)
        trace.record("youtube", started, error=error, **sent, status=status, body=body)
        if error is not None:
            if "quotaExceeded" in error_reasons(body):
                raise YouTubeQuotaError(error)
            raise YouTubeError(error)
        return body.get("items", []) if isinstance(body, dict) else []


def reply_error(method: str, status: int, body: object) -> str | None:
    """Why a reply is unusable, or None: the quota, a bad key, another status, no JSON."""
    if status == 200 and isinstance(body, dict):
        return None
    if status == 200:
        return f"YouTube answered something other than JSON for {method}."
    reasons = error_reasons(body)
    if "quotaExceeded" in reasons:
        return (
            f"YouTube's search quota is used up: {SEARCHES_PER_DAY} searches a day per Google "
            "Cloud project. Try again tomorrow, or pick another music source."
        )
    if reasons & {"API_KEY_INVALID", "keyInvalid"}:
        return "YouTube rejected the API key. Check YOUTUBE_API_KEY in .env."
    message = ""
    if isinstance(body, dict):
        message = (body.get("error") or {}).get("message") or ""
    return f"YouTube answered HTTP {status} for {method}. {message}".strip()


def error_reasons(body: object) -> set[str]:
    """The reason codes of a Google API error body, from `errors` and `details`."""
    if not isinstance(body, dict):
        return set()
    error = body.get("error") or {}
    if not isinstance(error, dict):
        return set()
    parts = [*(error.get("errors") or []), *(error.get("details") or [])]
    return {part.get("reason") for part in parts if isinstance(part, dict) and part.get("reason")}


def reject_reason(raw: dict, region: str) -> str | None:
    """Why the embedded player could not play a `videos.list` item in full, or None (D038)."""
    status = raw.get("status") or {}
    snippet = raw.get("snippet") or {}
    details = raw.get("contentDetails") or {}
    if status.get("privacyStatus") != "public":
        return "not public"
    if status.get("uploadStatus") != "processed":
        return "not processed"
    if status.get("embeddable") is not True:
        return "not embeddable"
    if snippet.get("liveBroadcastContent", "none") != "none":
        return "live or upcoming"
    if (details.get("contentRating") or {}).get("ytRating") == "ytAgeRestricted":
        return "age restricted"
    restriction = details.get("regionRestriction") or {}
    if region in (restriction.get("blocked") or []):
        return f"blocked in {region}"
    if "allowed" in restriction and region not in (restriction.get("allowed") or []):
        return f"not allowed in {region}"
    duration = parse_duration(details.get("duration") or "")
    if duration is None:
        return "no duration"
    if not MIN_DURATION_S <= duration <= MAX_DURATION_S:
        return f"lasts {duration:.0f} s"
    return None


def is_official(channel_title: str) -> bool:
    """Whether a channel name looks like the artist's own: a topic channel, VEVO, Official."""
    if channel_title.endswith(TOPIC_SUFFIX):
        return True
    lowered = channel_title.casefold()
    return VEVO in lowered or _OFFICIAL.search(lowered) is not None


def parse_duration(text: str) -> float | None:
    """Seconds of an ISO 8601 duration such as PT3M33S or PT1H2M3S. None when unreadable."""
    match = _DURATION.match(text)
    if match is None:
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return float(days * 86400 + hours * 3600 + minutes * 60 + seconds)


def to_video(raw: dict) -> Video:
    """A kept `videos.list` item as the cache keeps it. Call after `reject_reason`."""
    snippet = raw.get("snippet") or {}
    duration = parse_duration((raw.get("contentDetails") or {}).get("duration") or "")
    if duration is None:
        raise ValueError(f"YouTube video {raw.get('id')!r} has no readable duration.")
    return Video(
        id=raw["id"],
        title=snippet.get("title") or "",
        channel_id=snippet.get("channelId") or "",
        channel_title=snippet.get("channelTitle") or "",
        duration_s=duration,
    )


def to_track(proposal: SongProposal, video: Video, style: str | None) -> Track:
    """The Track the show plays: the proposal's names, the video's length and links (D038).

    `style` is "" for a show without one (D039), like `album` is (Track needs a str).
    """
    return Track(
        id=f"{ID_PREFIX}{video.id}",
        title=proposal.title,
        artist=proposal.artist,
        style=style or "",
        album="",
        duration_s=video.duration_s,
        license=LICENSE,
        license_url=LICENSE_URL,
        track_url=f"{WATCH_URL}{video.id}",
        artist_url=f"{CHANNEL_URL}{video.channel_id}",
        video_id=video.id,
    )


def cache_key(proposal: SongProposal) -> str:
    """A short file name for one proposal, the same whatever the case and spacing."""
    text = f"{_normalized(proposal.artist)}\n{_normalized(proposal.title)}"
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def read_cache(path: Path) -> CacheEntry | None:
    """The cache entry at `path` when it exists, reads, and is under CACHE_DAYS old.

    An expired entry is deleted, so API data never stays on disk past the 30 days.
    """
    if not path.exists():
        return None
    try:
        entry = CacheEntry.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        log.warning("Ignoring a broken YouTube cache file: %s", path)
        return None
    if datetime.now(UTC) - entry.fetched_at > timedelta(days=CACHE_DAYS):
        path.unlink(missing_ok=True)
        return None
    return entry


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())
