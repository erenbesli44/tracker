"""Build a single X/Twitter post per video: title → Öne Çıkanlar → Kısa Özet → channel → date.

X Premium allows posts up to 25,000 chars — no truncation needed.
"""

import json
from datetime import datetime


def build_post(
    *,
    title: str,
    channel_name: str | None,
    highlights_json: str | None,
    short_summary: str | None,
    published_at: datetime | None = None,
) -> str:
    """Return the full text for a single X post."""
    highlights: list[str] = []
    if highlights_json:
        try:
            parsed = json.loads(highlights_json)
            if isinstance(parsed, list):
                highlights = [str(h) for h in parsed if h]
        except (json.JSONDecodeError, TypeError):
            pass

    parts: list[str] = []

    parts.append((title or "—").strip())

    bullets = [h.strip() for h in highlights if h.strip()]
    if bullets:
        bullet_block = "\n".join(f"• {b}" for b in bullets)
        parts.append(f"Öne Çıkanlar\n\n{bullet_block}")

    summary = (short_summary or "").strip()
    if summary:
        parts.append(f"Kısa Özet\n\n{summary}")

    footer_parts: list[str] = []
    if channel_name and channel_name.strip():
        footer_parts.append(channel_name.strip())
    if published_at:
        footer_parts.append(published_at.strftime("%d.%m.%Y"))
    if footer_parts:
        parts.append(" · ".join(footer_parts))

    return "\n\n".join(parts)


# Keep build_thread as an alias so service.py works without changes
def build_thread(
    *,
    title: str,
    channel_name: str | None,
    highlights_json: str | None,
    short_summary: str | None,
    published_at: datetime | None = None,
    topic_mentions: list[dict],
) -> list[str]:
    """Returns a single-element list (one post, no thread)."""
    return [build_post(
        title=title,
        channel_name=channel_name,
        highlights_json=highlights_json,
        short_summary=short_summary,
        published_at=published_at,
    )]
