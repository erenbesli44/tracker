import json
import logging
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import yt_dlp

logger = logging.getLogger(__name__)

from sqlmodel import Session, select
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

from src.config import settings
from src.models import utc_now
from src.videos.models import Transcript, Video, VideoSummary
from src.videos.schemas import (
    TranscriptCreate,
    TranscriptSegment,
    VideoCreate,
    VideoSummaryCreate,
    VideoUpdate,
)

DEFAULT_TRANSCRIPT_LANGUAGES = ["tr", "en"]

try:
    from youtube_transcript_api._errors import IpBlocked, RequestBlocked

    _TRANSCRIPT_BLOCK_ERRORS: tuple[type[Exception], ...] = (IpBlocked, RequestBlocked)
except ImportError:
    _TRANSCRIPT_BLOCK_ERRORS = ()

_PROXY_STATE_LOCK = threading.Lock()
_PROXY_CURSOR = 0
_PROXY_STATE: dict[str, dict[str, float]] = {}
_YOUTUBE_REQUEST_SEMAPHORE = threading.BoundedSemaphore(
    max(1, int(settings.YOUTUBE_MAX_CONCURRENT_REQUESTS))
)


class YouTubeTranscriptFetchError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class YouTubeMetadataFetchError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _proxy_enabled() -> bool:
    return bool(
        settings.YOUTUBE_PROXY_ENABLED
        and settings.WEBSHARE_PROXY_USERNAME
        and settings.WEBSHARE_PROXY_PASSWORD
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _retry_count() -> int:
    return max(1, _safe_int(settings.YOUTUBE_PROXY_RETRIES, 3))


def _retry_delay_seconds(attempt_index: int) -> float:
    base = max(0.1, _safe_float(settings.YOUTUBE_PROXY_BACKOFF_SECONDS, 1.0))
    cap = max(base, _safe_float(settings.YOUTUBE_PROXY_MAX_BACKOFF_SECONDS, 8.0))
    return min(cap, (base * (2**attempt_index)) + random.uniform(0.0, base))


def _sleep_request_pacing() -> None:
    low = max(0.0, _safe_float(settings.YOUTUBE_REQUEST_MIN_DELAY_SECONDS, 0.5))
    high = max(low, _safe_float(settings.YOUTUBE_REQUEST_MAX_DELAY_SECONDS, 1.5))
    if high > 0:
        time.sleep(random.uniform(low, high))


@contextmanager
def _youtube_request_slot():
    _YOUTUBE_REQUEST_SEMAPHORE.acquire()
    try:
        yield
    finally:
        _YOUTUBE_REQUEST_SEMAPHORE.release()


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _build_proxy_url(host: str, port: int) -> str:
    username = quote(settings.WEBSHARE_PROXY_USERNAME or "", safe="")
    password = quote(settings.WEBSHARE_PROXY_PASSWORD or "", safe="")
    return f"http://{username}:{password}@{host}:{port}"


def _direct_proxy_targets() -> list[str]:
    targets = _parse_csv(settings.WEBSHARE_PROXY_LIST)
    if targets:
        return targets
    host = (settings.WEBSHARE_PROXY_HOST or "").strip()
    port = settings.WEBSHARE_PROXY_PORT
    if host and port:
        return [f"{host}:{port}"]
    return []


def _mark_proxy_success(proxy_label: str | None) -> None:
    if not proxy_label:
        return
    with _PROXY_STATE_LOCK:
        _PROXY_STATE[proxy_label] = {"failures": 0.0, "cooldown_until": 0.0}


def _mark_proxy_failure(proxy_label: str | None) -> None:
    if not proxy_label:
        return
    threshold = max(1, _safe_int(settings.YOUTUBE_PROXY_FAILURE_THRESHOLD, 2))
    cooldown = max(1, _safe_int(settings.YOUTUBE_PROXY_COOLDOWN_SECONDS, 180))
    with _PROXY_STATE_LOCK:
        state = _PROXY_STATE.setdefault(proxy_label, {"failures": 0.0, "cooldown_until": 0.0})
        failures = int(state.get("failures", 0.0)) + 1
        state["failures"] = float(failures)
        if failures >= threshold:
            state["failures"] = 0.0
            state["cooldown_until"] = time.time() + cooldown


def _select_direct_proxy(force_rotate: bool = False) -> tuple[str | None, str | None]:
    targets = _direct_proxy_targets()
    if not targets:
        return None, None

    now = time.time()
    with _PROXY_STATE_LOCK:
        global _PROXY_CURSOR
        size = len(targets)
        start = (_PROXY_CURSOR + (1 if force_rotate else 0)) % size
        selected_idx: int | None = None
        fallback_idx: int | None = None
        soonest_available: float | None = None

        for offset in range(size):
            idx = (start + offset) % size
            target = targets[idx]
            state = _PROXY_STATE.setdefault(target, {"failures": 0.0, "cooldown_until": 0.0})
            cooldown_until = float(state.get("cooldown_until", 0.0))
            if cooldown_until <= now:
                selected_idx = idx
                break
            if soonest_available is None or cooldown_until < soonest_available:
                soonest_available = cooldown_until
                fallback_idx = idx

        if selected_idx is None:
            selected_idx = fallback_idx if fallback_idx is not None else start

        _PROXY_CURSOR = selected_idx
        host_port = targets[selected_idx]

    host, sep, port_text = host_port.partition(":")
    if not sep:
        return None, None
    port = _safe_int(port_text, 0)
    if not host or port <= 0:
        return None, None
    return _build_proxy_url(host, port), host_port


def _select_proxy_url(force_rotate: bool = False) -> tuple[str | None, str | None]:
    if not _proxy_enabled():
        return None, None

    mode = (settings.YOUTUBE_PROXY_MODE or "direct").strip().lower()
    if mode == "rotating":
        host = (settings.WEBSHARE_PROXY_HOST or "p.webshare.io").strip()
        port = settings.WEBSHARE_PROXY_PORT or 80
        if not host or port <= 0:
            return None, None
        return _build_proxy_url(host, int(port)), f"{host}:{port}"
    return _select_direct_proxy(force_rotate=force_rotate)


def _no_ssl_session() -> "requests.Session":
    import requests
    session = requests.Session()
    session.verify = False
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def _build_transcript_client(force_rotate: bool = False) -> tuple[YouTubeTranscriptApi, str | None]:
    http_client = _no_ssl_session()

    if not _proxy_enabled():
        return YouTubeTranscriptApi(http_client=http_client), None

    mode = (settings.YOUTUBE_PROXY_MODE or "direct").strip().lower()
    if mode == "rotating":
        kwargs: dict[str, Any] = {
            "proxy_username": settings.WEBSHARE_PROXY_USERNAME,
            "proxy_password": settings.WEBSHARE_PROXY_PASSWORD,
        }
        locations = _parse_csv(settings.WEBSHARE_PROXY_FILTER_IP_LOCATIONS)
        if locations:
            kwargs["filter_ip_locations"] = locations
        return YouTubeTranscriptApi(proxy_config=WebshareProxyConfig(**kwargs), http_client=http_client), "webshare-rotating"

    proxy_url, proxy_label = _select_proxy_url(force_rotate=force_rotate)
    if proxy_url:
        proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
        return YouTubeTranscriptApi(proxy_config=proxy_config, http_client=http_client), proxy_label
    return YouTubeTranscriptApi(http_client=http_client), None


def _is_retryable_provider_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {exc}".lower()
    retryable_tokens = (
        "429",
        "too many",
        "rate limit",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "proxy",
        "service unavailable",
    )
    return any(token in message for token in retryable_tokens)


def _extract_info_with_yt_dlp(url: str, opts: dict[str, Any]) -> dict[str, Any]:
    attempts = _retry_count()
    last_error: Exception | None = None

    for attempt in range(attempts):
        proxy_url, proxy_label = _select_proxy_url(force_rotate=attempt > 0)
        attempt_opts = dict(opts)
        attempt_opts["nocheckcertificate"] = True
        if proxy_url:
            attempt_opts["proxy"] = proxy_url

        if attempt > 0:
            time.sleep(_retry_delay_seconds(attempt - 1))

        try:
            _sleep_request_pacing()
            with _youtube_request_slot():
                with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            _mark_proxy_success(proxy_label)
            return info
        except Exception as exc:
            last_error = exc
            _mark_proxy_failure(proxy_label)
            retryable = _is_retryable_provider_error(exc)
            logger.warning(
                "yt-dlp request failed attempt=%d/%d error=%s proxy=%s",
                attempt + 1,
                attempts,
                type(exc).__name__,
                proxy_label or "none",
            )
            if not retryable or attempt == attempts - 1:
                break

    if last_error is not None:
        raise last_error
    raise RuntimeError("yt-dlp request failed before execution")


def extract_info_with_yt_dlp(url: str, opts: dict[str, Any]) -> dict[str, Any]:
    """Shared yt-dlp extractor with proxy, pacing, and retry controls."""
    return _extract_info_with_yt_dlp(url, opts)


def _extract_youtube_id(url: str) -> str | None:
    pattern = r"(?:v=|youtu\.be/|/embed/|/v/)([A-Za-z0-9_-]{11})"
    match = re.search(pattern, url)
    return match.group(1) if match else None


def extract_youtube_id(url: str) -> str | None:
    return _extract_youtube_id(url)


def canonicalize_youtube_url(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        return url
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_youtube_channel_handle(author_url: str | None) -> str | None:
    if not author_url:
        return None

    parsed = urlparse(author_url)
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None

    if parts[0].startswith("@"):
        return parts[0]

    if parts[0] in {"channel", "c", "user"} and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"

    return parts[-1]


def fetch_youtube_metadata(video_url: str) -> dict[str, Any]:
    """Fetch video metadata via yt-dlp.

    Returns title, channel name/url/id, publish date, duration in one call.
    Replaces the previous oEmbed + HTML-scraping approach.
    """
    canonical_url = canonicalize_youtube_url(video_url)
    if not extract_youtube_id(canonical_url):
        raise YouTubeMetadataFetchError("Invalid YouTube URL")

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extractor_args": {"youtube": {"lang": ["tr"]}},
        "http_headers": {"Accept-Language": "tr-TR,tr;q=0.9"},
    }
    try:
        info = _extract_info_with_yt_dlp(canonical_url, opts)
    except Exception as exc:
        raise YouTubeMetadataFetchError("Failed to fetch YouTube metadata") from exc

    if not info:
        raise YouTubeMetadataFetchError("Empty response from YouTube")

    title = info.get("title")
    author_name = info.get("uploader") or info.get("channel")
    author_url = info.get("uploader_url") or info.get("channel_url") or ""

    if not isinstance(title, str) or not isinstance(author_name, str):
        raise YouTubeMetadataFetchError("Missing YouTube metadata fields")

    # Prefer timestamp (exact datetime) over upload_date (date-only YYYYMMDD).
    publish_date: datetime | None = None
    raw_timestamp = info.get("release_timestamp") or info.get("timestamp")
    if isinstance(raw_timestamp, (int, float)):
        publish_date = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).replace(tzinfo=None)
    else:
        upload_date = info.get("upload_date")
        if upload_date:
            try:
                publish_date = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=None)
            except ValueError:
                pass

    raw_duration = info.get("duration")
    duration: int | None = int(raw_duration) if isinstance(raw_duration, (int, float)) else None

    # yt-dlp provides the video's original language as ISO 639-1 code (e.g. "tr", "en").
    raw_language = info.get("language")
    language = raw_language.strip().lower() if isinstance(raw_language, str) and raw_language.strip() else None

    return {
        "title": title,
        "author_name": author_name,
        "author_url": author_url if isinstance(author_url, str) else "",
        "channel_id": info.get("channel_id") or "",
        "publish_date": publish_date,
        "duration": duration,
        "language": language,
    }


def _pick_thumbnail_url(thumbnails: list[dict[str, Any]], target_id: str) -> str | None:
    """Return the URL whose yt-dlp thumbnail id matches ``target_id`` (e.g. avatar_uncropped)."""
    for thumb in thumbnails:
        if isinstance(thumb, dict) and str(thumb.get("id") or "") == target_id:
            url = thumb.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def _pick_largest_thumbnail_url(thumbnails: list[dict[str, Any]]) -> str | None:
    """Fallback: return the URL of the largest thumbnail by width (then height)."""
    best: tuple[int, int, str] | None = None
    for thumb in thumbnails:
        if not isinstance(thumb, dict):
            continue
        url = thumb.get("url")
        if not isinstance(url, str) or not url:
            continue
        width = thumb.get("width") if isinstance(thumb.get("width"), int) else 0
        height = thumb.get("height") if isinstance(thumb.get("height"), int) else 0
        candidate = (width, height, url)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best else None


def extract_channel_profile_from_info(info: dict[str, Any]) -> dict[str, Any]:
    """Flatten yt-dlp channel info into the JSON payload we store on YouTubeChannel.

    Safe against partial / missing fields — every value is either the native
    type or ``None``. Meant to be serialised into ``channel_metadata``.
    """
    thumbnails_raw = info.get("thumbnails")
    thumbnails: list[dict[str, Any]] = thumbnails_raw if isinstance(thumbnails_raw, list) else []

    avatar_url = _pick_thumbnail_url(thumbnails, "avatar_uncropped")
    banner_url = _pick_thumbnail_url(thumbnails, "banner_uncropped")
    if avatar_url is None:
        # Avatars are square — fall back to the largest square-ish thumbnail.
        for thumb in thumbnails:
            if not isinstance(thumb, dict):
                continue
            width = thumb.get("width")
            height = thumb.get("height")
            if isinstance(width, int) and isinstance(height, int) and width == height:
                url = thumb.get("url")
                if isinstance(url, str) and url:
                    avatar_url = url
                    break
    if banner_url is None:
        banner_url = _pick_largest_thumbnail_url(
            [t for t in thumbnails if isinstance(t, dict) and t.get("id") != avatar_url]
        )

    def _int_or_none(value: Any) -> int | None:
        return int(value) if isinstance(value, (int, float)) else None

    def _str_or_none(value: Any) -> str | None:
        if isinstance(value, str):
            text_value = value.strip()
            return text_value or None
        return None

    def _str_list(value: Any) -> list[str] | None:
        if not isinstance(value, list):
            return None
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return cleaned or None

    compact_thumbnails: list[dict[str, Any]] = []
    for thumb in thumbnails:
        if not isinstance(thumb, dict):
            continue
        compact_thumbnails.append(
            {
                "id": _str_or_none(thumb.get("id")),
                "url": _str_or_none(thumb.get("url")),
                "width": _int_or_none(thumb.get("width")),
                "height": _int_or_none(thumb.get("height")),
            }
        )

    return {
        "youtube_channel_id": _str_or_none(info.get("channel_id")),
        "channel_url": _str_or_none(info.get("channel_url")),
        "channel_name": _str_or_none(info.get("channel") or info.get("uploader")),
        "uploader_id": _str_or_none(info.get("uploader_id")),
        "uploader_url": _str_or_none(info.get("uploader_url")),
        "subscriber_count": _int_or_none(info.get("channel_follower_count")),
        "view_count": _int_or_none(info.get("view_count")),
        "video_count": _int_or_none(info.get("playlist_count")),
        "is_verified": info.get("channel_is_verified") if isinstance(info.get("channel_is_verified"), bool) else None,
        "description": _str_or_none(info.get("description")),
        "tags": _str_list(info.get("tags")),
        "avatar_url": avatar_url,
        "banner_url": banner_url,
        "thumbnails": compact_thumbnails or None,
        "fetched_at": utc_now().isoformat(),
    }


def fetch_channel_profile(channel_input: str) -> dict[str, Any]:
    """Fetch profile data (avatar, banner, subs, …) for a YouTube channel.

    ``channel_input`` may be a channel ID (``UC…``), a handle (``@name``), or
    a full channel URL. Uses yt-dlp in extract_flat mode so we only fetch
    channel-level info without enumerating the full playlist.
    """
    value = channel_input.strip()
    if value.startswith("UC") and len(value) == 24:
        url = f"https://www.youtube.com/channel/{value}"
    elif value.startswith("@"):
        url = f"https://www.youtube.com/{value}"
    elif value.startswith("http://") or value.startswith("https://"):
        url = value
    else:
        url = f"https://www.youtube.com/@{value.lstrip('@')}"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": 1,
        "skip_download": True,
        "extractor_args": {"youtube": {"lang": ["tr"]}},
        "http_headers": {"Accept-Language": "tr-TR,tr;q=0.9"},
    }
    try:
        info = _extract_info_with_yt_dlp(url, opts)
    except Exception as exc:
        raise YouTubeMetadataFetchError(
            f"Failed to fetch YouTube channel profile for {channel_input}"
        ) from exc
    if not info:
        raise YouTubeMetadataFetchError("Empty response from YouTube channel page")
    return extract_channel_profile_from_info(info)


def create(session: Session, data: VideoCreate) -> Video:
    canonical_url = canonicalize_youtube_url(data.video_url)
    video_id = extract_youtube_id(canonical_url) or canonical_url
    video = Video(
        channel_id=data.channel_id,
        person_id=data.person_id,
        video_url=canonical_url,
        video_id=video_id,
        title=data.title,
        published_at=data.published_at,
        duration=data.duration,
    )
    session.add(video)
    session.commit()
    session.refresh(video)
    return video


def get_by_id(session: Session, video_id: int) -> Video | None:
    return session.get(Video, video_id)


def update(session: Session, video: Video, data: VideoUpdate) -> Video:
    if data.title is not None:
        video.title = data.title
    if data.published_at is not None:
        video.published_at = data.published_at
    if data.duration is not None:
        video.duration = data.duration
    session.add(video)
    session.commit()
    session.refresh(video)
    return video


def get_by_video_id(session: Session, video_id: str) -> Video | None:
    return session.exec(select(Video).where(Video.video_id == video_id)).first()


def get_by_url(session: Session, url: str) -> Video | None:
    video_id = extract_youtube_id(url)
    if video_id:
        by_video_id = get_by_video_id(session, video_id)
        if by_video_id:
            return by_video_id

    canonical_url = canonicalize_youtube_url(url)
    return session.exec(select(Video).where(Video.video_url == canonical_url)).first()


def fetch_publish_date_from_html(video_url: str) -> datetime | None:
    """Fetch publish date by parsing the YouTube watch page HTML.

    Falls back to this when yt-dlp is blocked on the server.
    Looks for datePublished in JSON-LD or meta tags.
    """
    canonical = canonicalize_youtube_url(video_url)
    request = Request(canonical, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    _sleep_request_pacing()
    with _youtube_request_slot():
        with urlopen(request, timeout=15) as response:  # nosec B310
            html = response.read().decode("utf-8", errors="ignore")

    # Match ISO 8601 datetimes: "2026-04-05T03:47:47-07:00" or "2026-04-05"
    for pattern in (
        r'"datePublished"\s*:\s*"([^"]+)"',
        r'itemprop="datePublished"\s+content="([^"]+)"',
        r'"publishDate"\s*:\s*"(\d{4}-\d{2}-\d{2}[^"]*)"',
        r'"uploadDate"\s*:\s*"([^"]+)"',
    ):
        match = re.search(pattern, html)
        if match:
            raw = match.group(1).strip()
            try:
                parsed = datetime.fromisoformat(raw)
                if parsed.tzinfo is not None:
                    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
                return parsed
            except ValueError:
                continue

    return None


def backfill_published_dates(session: Session) -> list[dict]:
    """Fetch and update published_at for all videos missing it."""
    videos = list(
        session.exec(select(Video).where(Video.published_at.is_(None))).all()
    )
    results: list[dict] = []
    for video in videos:
        publish_date: datetime | None = None
        errors: list[str] = []

        # Try yt-dlp first (most precise).
        try:
            metadata = fetch_youtube_metadata(video.video_url)
            publish_date = metadata.get("publish_date")
        except YouTubeMetadataFetchError as exc:
            errors.append(f"yt-dlp: {exc.detail}")

        # Fall back to HTML parsing when yt-dlp fails.
        if publish_date is None:
            try:
                publish_date = fetch_publish_date_from_html(video.video_url)
                if publish_date is None:
                    errors.append("html: no date found in page")
            except Exception as exc:
                errors.append(f"html: {exc}")

        if publish_date:
            video.published_at = publish_date
            session.add(video)
            session.flush()
            results.append({"video_id": video.id, "status": "updated", "published_at": publish_date.isoformat()})
        else:
            results.append({"video_id": video.id, "status": "failed", "published_at": None, "errors": errors})

    if results:
        session.commit()
    return results


def list_by_person(session: Session, person_id: int) -> list[Video]:
    return list(
        session.exec(
            select(Video)
            .where(Video.person_id == person_id)
            .order_by(
                Video.published_at.desc().nullslast(),
                Video.created_at.desc(),
            )
        ).all()
    )


def list_by_channel(session: Session, channel_id: int) -> list[Video]:
    return list(
        session.exec(
            select(Video)
            .where(Video.channel_id == channel_id)
            .order_by(
                Video.published_at.desc().nullslast(),
                Video.created_at.desc(),
            )
        ).all()
    )


def list_all(session: Session) -> list[Video]:
    return list(
        session.exec(
            select(Video).order_by(
                Video.published_at.desc().nullslast(),
                Video.created_at.desc(),
            )
        ).all()
    )


def get_transcript(session: Session, video_id: int) -> Transcript | None:
    return session.exec(select(Transcript).where(Transcript.video_id == video_id)).first()


def _serialize_transcript_segments(
    segments: list[TranscriptSegment] | list[dict[str, Any]] | list[Any] | None,
) -> str | None:
    if not segments:
        return None

    normalized: list[dict[str, Any]] = []
    for item in segments:
        if isinstance(item, TranscriptSegment):
            payload = item.model_dump()
        elif isinstance(item, dict):
            payload = item
        elif hasattr(item, "model_dump"):
            payload = item.model_dump()
        else:
            continue

        text = str(payload.get("text", "")).strip()
        if not text:
            continue

        try:
            start = float(payload.get("start", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(payload.get("duration", 0.0))
        except (TypeError, ValueError):
            duration = 0.0

        normalized.append(
            {
                "start": max(0.0, start),
                "duration": max(0.0, duration),
                "text": text,
            }
        )

    if not normalized:
        return None
    return json.dumps(normalized, ensure_ascii=False)


def parse_transcript_segments(raw: str | None) -> list[dict[str, Any]] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None

    normalized: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        try:
            start = float(item.get("start", 0.0))
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(item.get("duration", 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        normalized.append(
            {
                "start": max(0.0, start),
                "duration": max(0.0, duration),
                "text": text,
            }
        )
    return normalized or None


def add_transcript(session: Session, video: Video, data: TranscriptCreate) -> Transcript:
    transcript = Transcript(
        video_id=video.id,
        raw_text=data.raw_text,
        segments_json=_serialize_transcript_segments(data.segments),
        language=data.language,
    )
    session.add(transcript)
    session.commit()
    session.refresh(transcript)
    return transcript


def update_transcript(
    session: Session,
    transcript: Transcript,
    *,
    raw_text: str,
    language: str,
    segments: list[TranscriptSegment] | list[dict[str, Any]] | list[Any] | None = None,
) -> Transcript:
    transcript.raw_text = raw_text
    transcript.segments_json = _serialize_transcript_segments(segments)
    transcript.language = language
    session.add(transcript)
    session.commit()
    session.refresh(transcript)
    return transcript


def _serialize_highlights(highlights: list[str] | None) -> str | None:
    if highlights is None:
        return None
    return json.dumps(highlights)


def parse_highlights(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return [str(item) for item in parsed]


def get_summary(session: Session, video_id: int) -> VideoSummary | None:
    return session.exec(select(VideoSummary).where(VideoSummary.video_id == video_id)).first()


def add_summary(session: Session, video: Video, data: VideoSummaryCreate) -> VideoSummary:
    summary = VideoSummary(
        video_id=video.id,
        short_summary=data.short_summary,
        long_summary=data.long_summary,
        highlights=_serialize_highlights(data.highlights),
        language=data.language,
        source=data.source,
    )
    session.add(summary)
    session.commit()
    session.refresh(summary)
    return summary


def update_summary(
    session: Session,
    summary: VideoSummary,
    data: VideoSummaryCreate,
) -> VideoSummary:
    summary.short_summary = data.short_summary
    summary.long_summary = data.long_summary
    summary.highlights = _serialize_highlights(data.highlights)
    summary.language = data.language
    summary.source = data.source
    summary.updated_at = utc_now()
    session.add(summary)
    session.commit()
    session.refresh(summary)
    return summary


def fetch_transcript_from_youtube(
    video_id: str,
    languages: list[str] | None = None,
) -> dict[str, Any]:
    requested_languages = languages or list(DEFAULT_TRANSCRIPT_LANGUAGES)
    attempts = _retry_count()
    fetched: Any | None = None

    for attempt in range(attempts):
        api, proxy_label = _build_transcript_client(force_rotate=attempt > 0)

        if attempt > 0:
            time.sleep(_retry_delay_seconds(attempt - 1))

        try:
            _sleep_request_pacing()
            with _youtube_request_slot():
                fetched = api.fetch(video_id, languages=requested_languages)
            _mark_proxy_success(proxy_label)
            break
        except (TranscriptsDisabled, NoTranscriptFound) as exc:
            raise YouTubeTranscriptFetchError(
                code="transcript_unavailable",
                detail="No transcript is available for this video in the requested languages.",
            ) from exc
        except VideoUnavailable as exc:
            raise YouTubeTranscriptFetchError(
                code="video_unavailable",
                detail="YouTube video is unavailable.",
            ) from exc
        except Exception as exc:
            _mark_proxy_failure(proxy_label)
            retryable = isinstance(exc, _TRANSCRIPT_BLOCK_ERRORS) or _is_retryable_provider_error(exc)
            logger.warning(
                "Transcript fetch failed attempt=%d/%d error=%s proxy=%s",
                attempt + 1,
                attempts,
                type(exc).__name__,
                proxy_label or "none",
            )
            if retryable and attempt < attempts - 1:
                continue
            raise YouTubeTranscriptFetchError(
                code="provider_error",
                detail="Failed to fetch transcript from YouTube provider.",
            ) from exc

    if fetched is None:
        raise YouTubeTranscriptFetchError(
            code="provider_error",
            detail="Failed to fetch transcript from YouTube provider: empty response.",
        )

    segments = [
        {
            "start": snippet.start,
            "duration": snippet.duration,
            "text": snippet.text,
        }
        for snippet in fetched.snippets
        if snippet.text and snippet.text.strip()
    ]
    full_text = " ".join(segment["text"] for segment in segments).strip()
    if not full_text:
        raise YouTubeTranscriptFetchError(
            code="transcript_unavailable",
            detail="Transcript was fetched but contains no usable text.",
        )

    return {
        "full_text": full_text,
        "segments": segments,
        "language": getattr(fetched, "language_code", None) or "tr",
        "is_generated": getattr(fetched, "is_generated", None),
        "languages_tried": requested_languages,
    }
