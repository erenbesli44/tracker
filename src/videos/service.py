import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
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

from src.models import utc_now
from src.videos.models import Transcript, Video, VideoSummary
from src.videos.schemas import TranscriptCreate, VideoCreate, VideoSummaryCreate

DEFAULT_TRANSCRIPT_LANGUAGES = ["tr", "en"]


class YouTubeTranscriptFetchError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class YouTubeMetadataFetchError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(canonical_url, download=False)
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
    request = Request(canonical, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(request, timeout=15) as response:  # nosec B310
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

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

        # Try yt-dlp first (most precise).
        try:
            metadata = fetch_youtube_metadata(video.video_url)
            publish_date = metadata.get("publish_date")
        except YouTubeMetadataFetchError:
            pass

        # Fall back to HTML parsing when yt-dlp fails.
        if publish_date is None:
            publish_date = fetch_publish_date_from_html(video.video_url)

        if publish_date:
            video.published_at = publish_date
            session.add(video)
            session.flush()
            results.append({"video_id": video.id, "status": "updated", "published_at": publish_date.isoformat()})
        else:
            results.append({"video_id": video.id, "status": "failed", "published_at": None})

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


def add_transcript(session: Session, video: Video, data: TranscriptCreate) -> Transcript:
    transcript = Transcript(
        video_id=video.id,
        raw_text=data.raw_text,
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
) -> Transcript:
    transcript.raw_text = raw_text
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
    api = YouTubeTranscriptApi()
    requested_languages = languages or list(DEFAULT_TRANSCRIPT_LANGUAGES)

    try:
        fetched = api.fetch(video_id, languages=requested_languages)
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
        raise YouTubeTranscriptFetchError(
            code="provider_error",
            detail=f"Failed to fetch transcript from YouTube provider: {type(exc).__name__}: {exc}",
        ) from exc

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
