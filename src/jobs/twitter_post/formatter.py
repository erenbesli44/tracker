"""Build a single X/Twitter post per video: channel (bold) → title → highlights → Kısa Özet → date.

X Premium allows posts up to 25,000 chars — no truncation needed.
"""

import json
from datetime import datetime

_BOLD_MAP = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵",
)


def _bold(text: str) -> str:
    """Convert ASCII alphanumeric chars to Unicode bold sans-serif; leave others unchanged."""
    return text.translate(_BOLD_MAP)


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

    if channel_name and channel_name.strip():
        parts.append(_bold(channel_name.strip()))

    parts.append((title or "—").strip())

    bullets = [h.strip() for h in highlights if h.strip()]
    if bullets:
        parts.append("\n\n".join(f"• {b}" for b in bullets))

    summary = (short_summary or "").strip()
    if summary:
        parts.append(f"Kısa Özet\n\n{summary}")

    if published_at:
        parts.append(published_at.strftime("%d.%m.%Y"))

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
