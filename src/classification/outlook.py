"""Instrument outlook: the speaker's forward-looking price direction per mention.

The analysis step tags *topics* broadly and its stance is about the economy as
much as the instrument (e.g. "dolar 80 liraya çıkabilir" was stored as
bearish). This step asks one narrow question per finance mention — "what does
the speaker expect THIS instrument's price to do next, and do they say it
outright?" — and stores the answer next to the mention.

Only mentions of tracked instruments are classified; everything else keeps
outlook = NULL. Failures never break ingestion: the mention stays NULL and the
backfill script (scripts/backfill_outlook.py) picks it up later.
"""

import logging
from typing import Any

from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.llm.prompts import OUTLOOK_PROMPT_VERSION
from src.topics.models import Topic
from src.videos import service as videos_service
from src.videos.models import Transcript, Video

logger = logging.getLogger(__name__)

# topic slug -> instrument description shown to the model
INSTRUMENTS: dict[str, str] = {
    "doviz-kur": "Dolar/TL kuru (USD/TRY)",
    "bist-turk-piyasalari": "BIST 100 endeksi (Borsa İstanbul geneli)",
    "altin": "Altın fiyatı (ons veya gram altın)",
    "gumus": "Gümüş fiyatı",
    "kripto-paralar": "Bitcoin fiyatı",
    "amerikan-piyasalari": "ABD hisse endeksleri (S&P 500, Nasdaq, Dow Jones)",
    "petrol-enerji": "Petrol fiyatı (Brent / WTI)",
}

_VALID = {"up", "down", "flat", "none"}
_WINDOW_PAD_SECONDS = 45
_EXCERPT_MAX_CHARS = 3500
_QUOTE_MAX_CHARS = 240


def _seconds(timecode: str | None) -> float | None:
    if not timecode:
        return None
    try:
        parts = [float(p) for p in timecode.split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def transcript_excerpt(
    segments: list[dict[str, Any]] | None,
    start_time: str | None,
    end_time: str | None,
) -> str:
    """Transcript text inside the mention's time window (padded), capped."""
    if not segments:
        return ""
    start = _seconds(start_time)
    end = _seconds(end_time)
    if start is None:
        return ""
    lo = max(0.0, start - _WINDOW_PAD_SECONDS)
    hi = (end if end is not None and end > start else start + 240) + _WINDOW_PAD_SECONDS
    words = [
        str(s.get("text", "")).strip()
        for s in segments
        if lo <= float(s.get("start", 0.0)) <= hi
    ]
    text = " ".join(w for w in words if w)
    return text[:_EXCERPT_MAX_CHARS]


def _strip_topic_prefix(summary: str) -> str:
    head, sep, tail = summary.partition(":")
    return tail.strip() if sep and len(head) < 40 else summary.strip()


def build_items_block(rows: list[tuple[TopicMention, str, str]]) -> str:
    """rows: (mention, instrument description, transcript excerpt)."""
    blocks = []
    for mention, instrument, excerpt in rows:
        blocks.append(
            f"[mention_id={mention.id}] Araç: {instrument}\n"
            f"Özet: {_strip_topic_prefix(mention.summary)}\n"
            f"Transkript kesiti: {excerpt or '(yok)'}"
        )
    return "\n\n".join(blocks)


def parse_outlook_items(payload: dict[str, Any], mention_ids: set[int]) -> dict[int, dict]:
    """Validate the model output; anything malformed is dropped (stays unclassified)."""
    out: dict[int, dict] = {}
    items = payload.get("items")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            mid = int(item.get("mention_id"))
        except (TypeError, ValueError):
            continue
        if mid not in mention_ids:
            continue
        outlook = str(item.get("outlook", "")).strip().lower()
        if outlook not in _VALID:
            continue
        explicit = item.get("explicit") is True and outlook != "none"
        out[mid] = {
            "outlook": outlook,
            "outlook_explicit": explicit,
            "outlook_horizon": (str(item.get("horizon") or "").strip()[:80] or None),
            "outlook_quote": (str(item.get("quote") or "").strip()[:_QUOTE_MAX_CHARS] or None),
        }
    return out


def classify_video(session: Session, video: Video, *, dry_run: bool = False) -> dict[int, dict]:
    """Classify every tracked-instrument mention of one video. Returns {mention_id: fields}."""
    rows = session.exec(
        select(TopicMention, Topic)
        .join(Topic, TopicMention.topic_id == Topic.id)
        .where(TopicMention.video_id == video.id)
        .where(Topic.slug.in_(list(INSTRUMENTS)))
        # Load only pre-existing columns so a dry run works against a schema
        # that does not have the outlook columns yet.
        .options(
            load_only(
                TopicMention.id,
                TopicMention.video_id,
                TopicMention.topic_id,
                TopicMention.summary,
                TopicMention.start_time,
                TopicMention.end_time,
            )
        )
    ).all()
    if not rows:
        return {}

    transcript = session.exec(select(Transcript).where(Transcript.video_id == video.id)).first()
    segments = (
        videos_service.parse_transcript_segments(transcript.segments_json) if transcript else None
    )
    items = [
        (m, INSTRUMENTS[t.slug], transcript_excerpt(segments, m.start_time, m.end_time))
        for m, t in rows
    ]
    speaker = video.title or ""
    if video.channel_id is not None:
        from src.channels.models import YouTubeChannel

        channel = session.get(YouTubeChannel, video.channel_id)
        speaker = channel.name if channel else speaker

    payload = llm_service.generate_outlook_json(
        video_title=video.title or "",
        speaker_name=speaker,
        published_at=video.published_at.isoformat() if video.published_at else "",
        items_block=build_items_block(items),
    )
    results = parse_outlook_items(payload, {m.id for m, _ in rows})

    if not dry_run:
        for mention, _ in rows:
            fields = results.get(mention.id)
            if fields is None:
                continue
            for k, v in fields.items():
                setattr(mention, k, v)
            mention.outlook_version = OUTLOOK_PROMPT_VERSION
            session.add(mention)
        session.commit()
    return results


def classify_video_safely(session: Session, video: Video) -> None:
    """Ingestion hook: never raises."""
    try:
        classify_video(session, video)
    except Exception:  # noqa: BLE001 — ingestion must not fail on this step
        session.rollback()
        logger.warning("Outlook classification failed for video_id=%s", video.id, exc_info=True)
