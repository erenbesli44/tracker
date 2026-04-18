"""
Compare current fetching approach vs yt-dlp for YouTube metadata and transcripts.

Usage:
    uv run python scripts/compare_youtube_fetchers.py [VIDEO_URL]

Default test video: a public video with manual + auto captions.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable

# ── test target ──────────────────────────────────────────────────────────────
DEFAULT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Astley – reliable public video
VIDEO_URL = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL


def _extract_video_id(url: str) -> str | None:
    match = re.search(r"(?:v=|youtu\.be/|/embed/|/v/)([A-Za-z0-9_-]{11})", url)
    return match.group(1) if match else None


VIDEO_ID = _extract_video_id(VIDEO_URL)
print(f"\n{'='*60}")
print(f"  Comparing YouTube fetchers")
print(f"  URL: {VIDEO_URL}")
print(f"  ID:  {VIDEO_ID}")
print(f"{'='*60}\n")


# ── APPROACH A: current (oEmbed + HTML scraping + youtube-transcript-api) ────

def approach_a_metadata(video_url: str) -> dict:
    canonical = f"https://www.youtube.com/watch?v={_extract_video_id(video_url)}"
    endpoint = f"https://www.youtube.com/oembed?url={quote_plus(canonical)}&format=json"
    with urlopen(endpoint, timeout=10) as r:  # nosec
        payload = json.loads(r.read().decode())

    # publish date via HTML scraping
    req = Request(canonical, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=10) as r:  # nosec
            html = r.read().decode("utf-8", errors="ignore")
        match = re.search(r'"publishDate"\s*:\s*"([^"]+)"', html)
        publish_date = match.group(1) if match else None
    except Exception:
        publish_date = None

    # duration via HTML scraping
    dur_match = re.search(r'"lengthSeconds"\s*:\s*"(\d+)"', html) if html else None
    duration_seconds = int(dur_match.group(1)) if dur_match else None

    return {
        "title": payload.get("title"),
        "channel_name": payload.get("author_name"),
        "channel_url": payload.get("author_url"),
        "publish_date": publish_date,
        "duration_seconds": duration_seconds,
        "description": None,       # not available via oEmbed
        "thumbnail_url": payload.get("thumbnail_url"),
        "view_count": None,        # not available via oEmbed
        "channel_id": None,        # not available via oEmbed
    }


def approach_a_transcript(video_id: str, languages: list[str] = None) -> dict:
    languages = languages or ["tr", "en"]
    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=languages)
    segments = [
        {"start": s.start, "duration": s.duration, "text": s.text}
        for s in fetched.snippets if s.text and s.text.strip()
    ]
    return {
        "full_text": " ".join(s["text"] for s in segments).strip(),
        "segments": segments,
        "language": getattr(fetched, "language_code", None),
        "is_generated": getattr(fetched, "is_generated", None),
    }


# ── APPROACH B: yt-dlp metadata + youtube-transcript-api transcript ──────────

def approach_b_metadata(video_url: str) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)

    upload_date = info.get("upload_date")  # YYYYMMDD string
    publish_date = None
    if upload_date:
        try:
            publish_date = datetime.strptime(upload_date, "%Y%m%d").date().isoformat()
        except ValueError:
            pass

    thumbnail = info.get("thumbnail") or (
        info.get("thumbnails", [{}])[-1].get("url") if info.get("thumbnails") else None
    )

    return {
        "title": info.get("title"),
        "channel_name": info.get("uploader") or info.get("channel"),
        "channel_url": info.get("uploader_url") or info.get("channel_url"),
        "channel_id": info.get("channel_id"),
        "publish_date": publish_date,
        "duration_seconds": info.get("duration"),
        "description": (info.get("description") or "")[:300] + "…",
        "thumbnail_url": thumbnail,
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "tags": info.get("tags", [])[:5],
        "available_subtitles": list(info.get("subtitles", {}).keys()),
        "available_auto_captions": list(info.get("automatic_captions", {}).keys()),
    }


def approach_b_transcript(video_id: str, languages: list[str] = None) -> dict:
    """Same youtube-transcript-api, so results are identical."""
    return approach_a_transcript(video_id, languages)


# ── run and report ────────────────────────────────────────────────────────────

def section(title: str):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


def run_timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        return result, elapsed, None
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return None, elapsed, exc


# ── METADATA ─────────────────────────────────────────────────────────────────
section("APPROACH A — Metadata (oEmbed + HTML scraping)")
meta_a, t_a, err_a = run_timed(approach_a_metadata, VIDEO_URL)
if err_a:
    print(f"  ERROR ({t_a:.2f}s): {err_a}")
else:
    print(f"  Time: {t_a:.2f}s")
    for k, v in meta_a.items():
        available = "✓" if v not in (None, [], "") else "✗ missing"
        print(f"  {k:<30} {available}  {str(v)[:60] if v else ''}")

section("APPROACH B — Metadata (yt-dlp)")
meta_b, t_b, err_b = run_timed(approach_b_metadata, VIDEO_URL)
if err_b:
    print(f"  ERROR ({t_b:.2f}s): {err_b}")
else:
    print(f"  Time: {t_b:.2f}s")
    for k, v in meta_b.items():
        available = "✓" if v not in (None, [], "") else "✗ missing"
        print(f"  {k:<30} {available}  {str(v)[:60] if v else ''}")

# ── TRANSCRIPT ────────────────────────────────────────────────────────────────
section("TRANSCRIPT (youtube-transcript-api) — same for both approaches")
tr, t_tr, err_tr = run_timed(approach_a_transcript, VIDEO_ID)
if err_tr:
    print(f"  ERROR ({t_tr:.2f}s): {err_tr}")
else:
    print(f"  Time:          {t_tr:.2f}s")
    print(f"  language:      {tr['language']}")
    print(f"  is_generated:  {tr['is_generated']}")
    print(f"  segments:      {len(tr['segments'])}")
    print(f"  text length:   {len(tr['full_text'])} chars")
    print(f"  preview:       {tr['full_text'][:120]}…")

# ── SUMMARY ───────────────────────────────────────────────────────────────────
section("SUMMARY")
print(f"""
  Approach A (current) — {t_a:.2f}s for metadata
  ─ oEmbed:        fast, stable, but only title + channel name/url
  ─ HTML scraping: fragile (publish_date, duration) — breaks on YouTube HTML changes
  ─ RSS feed:      used for channel listing, capped at ~15 videos
  ─ Missing:       description, channel_id, view_count, thumbnail (no oembed v2)

  Approach B (yt-dlp) — {t_b:.2f}s for metadata
  ─ Single call:   title, channel_id, description, duration, publish_date,
                   view_count, like_count, tags, thumbnail, subtitle list
  ─ No scraping:   yt-dlp maintains YouTube extraction so you don't have to
  ─ Channel scan:  --flat-playlist gives unlimited video list vs RSS 15-cap
  ─ Trade-off:     heavier dependency (~3 MB), slightly slower single-video call

  Transcript: youtube-transcript-api is the right tool regardless — keep it.

  Recommendation: replace oEmbed + HTML scraping with yt-dlp for metadata.
  Keep youtube-transcript-api for transcripts.
""")
