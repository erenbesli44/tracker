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
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from src.classification import quote_clean
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.llm.prompts import OUTLOOK_PROMPT_VERSION, PARTICIPANTS_PROMPT_VERSION
from src.persons import detector
from src.persons import participants as participants_service
from src.persons.models import VideoParticipants
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


def classify_video(
    session: Session,
    video: Video,
    *,
    dry_run: bool = False,
    mention_ids: set[int] | None = None,
) -> dict[int, dict]:
    """Classify the tracked-instrument mentions of one video. Returns {mention_id: fields}.

    ``mention_ids`` limits the step to those mentions; the others keep their views.
    """
    stmt = (
        select(TopicMention, Topic)
        .join(Topic, TopicMention.topic_id == Topic.id)
        .where(TopicMention.video_id == video.id)
        .where(Topic.slug.in_(list(INSTRUMENTS)))
    )
    if mention_ids is not None:
        if not mention_ids:
            return {}
        stmt = stmt.where(TopicMention.id.in_(list(mention_ids)))
    rows = session.exec(
        stmt
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
    channel = None
    if video.channel_id is not None:
        from src.channels.models import YouTubeChannel

        channel = session.get(YouTubeChannel, video.channel_id)
        speaker = channel.name if channel else speaker

    # Multi-guest channels: name the guests so each view can be attributed (OI-018).
    participants = None
    if channel is not None and detector.is_multi_guest_channel(channel.channel_handle):
        participants = participants_service.detect_participants(
            session, video, channel.name, segments, dry_run=dry_run
        )
        speaker = participants_service.speaker_context(participants)

    payload = llm_service.generate_outlook_json(
        video_title=video.title or "",
        speaker_name=speaker,
        published_at=video.published_at.isoformat() if video.published_at else "",
        items_block=build_items_block(items),
    )
    results = parse_outlook_items(payload, {m.id for m, _ in rows})
    add_clean_quotes(results, items)
    speakers = parse_speakers(payload, {m.id for m, _ in rows})
    if participants is not None:
        for mid, fields in results.items():
            fields["speaker"] = speakers.get(mid)
            fields["person_id"] = participants_service.speaker_person_id(
                speakers.get(mid), participants
            )

    if not dry_run:
        for mention, _ in rows:
            fields = results.get(mention.id)
            if participants is not None:
                # Unattributed views must not stay under the video's headline guest.
                mention.person_id = fields.get("person_id") if fields else None
            if fields is None:
                session.add(mention)
                continue
            for k in (
                "outlook",
                "outlook_explicit",
                "outlook_horizon",
                "outlook_quote",
                "outlook_quote_clean",
            ):
                setattr(mention, k, fields[k])
            mention.outlook_version = OUTLOOK_PROMPT_VERSION
            session.add(mention)
        if participants is not None and mention_ids is None:
            _clear_other_mention_persons(session, video.id, {m.id for m, _ in rows})
        session.commit()
    return results


def add_clean_quotes(
    results: dict[int, dict], items: list[tuple[TopicMention, str, str]]
) -> None:
    """Set outlook_quote_clean on each result. An LLM failure leaves it NULL for the backfill;
    a rejected edit stores the verbatim quote (checked, nothing safe to change)."""
    todo = [
        {
            "id": mention.id,
            "quote": results[mention.id]["outlook_quote"],
            "instrument": instrument,
            "context": quote_clean.quote_context(results[mention.id]["outlook_quote"], excerpt),
        }
        for mention, instrument, excerpt in items
        if results.get(mention.id, {}).get("outlook_quote")
    ]
    cleaned: dict[int, str | None] | None = {}
    if todo:
        try:
            cleaned = quote_clean.clean_quotes(todo)
        except Exception:  # noqa: BLE001 — clean-up is optional; classification stands
            logger.warning("Quote clean-up failed", exc_info=True)
            cleaned = None
    for fields in results.values():
        fields["outlook_quote_clean"] = None
    if cleaned is None:
        return
    for item in todo:
        results[item["id"]]["outlook_quote_clean"] = cleaned.get(item["id"]) or item["quote"]


def parse_speakers(payload: dict[str, Any], mention_ids: set[int]) -> dict[int, str]:
    """mention_id -> speaker name as the model wrote it (multi-guest attribution)."""
    out: dict[int, str] = {}
    items = payload.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            mid = int(item.get("mention_id"))
        except (TypeError, ValueError):
            continue
        speaker = str(item.get("speaker") or "").strip()
        if mid in mention_ids and speaker:
            out[mid] = speaker[:120]
    return out


def _clear_other_mention_persons(session: Session, video_id: int, classified: set[int]) -> None:
    """Non-instrument mentions of a multi-guest video are not attributed.

    Drop the headline guest from them.
    """
    others = session.exec(
        select(TopicMention)
        .where(TopicMention.video_id == video_id)
        .where(TopicMention.person_id.is_not(None))
        .options(load_only(TopicMention.id, TopicMention.person_id))
    ).all()
    for mention in others:
        if mention.id not in classified:
            mention.person_id = None
            session.add(mention)


def classify_video_safely(session: Session, video: Video) -> None:
    """Ingestion hook: never raises."""
    try:
        classify_video(session, video)
    except Exception:  # noqa: BLE001 — ingestion must not fail on this step
        session.rollback()
        logger.warning("Outlook classification failed for video_id=%s", video.id, exc_info=True)


# ---------------------------------------------------------------------------
# Backfill (shared by scripts/backfill_outlook.py and POST /outlook/backfill)
# ---------------------------------------------------------------------------



def pending_video_ids(session: Session, limit: int, redo_all: bool = False) -> list[int]:
    """Videos with at least one instrument mention not on the current prompt version."""
    stmt = (
        select(Video.id, func.max(Video.published_at))
        .join(TopicMention, TopicMention.video_id == Video.id)
        .join(Topic, TopicMention.topic_id == Topic.id)
        .where(Topic.slug.in_(list(INSTRUMENTS)))
    )
    if not redo_all:
        stmt = stmt.where(
            (TopicMention.outlook_version.is_(None))
            | (TopicMention.outlook_version != OUTLOOK_PROMPT_VERSION)
        )
    stmt = stmt.group_by(Video.id).order_by(
        func.max(Video.published_at).desc().nulls_last(), Video.id.desc()
    )
    return [row[0] for row in session.exec(stmt.limit(limit)).all()]


def pending_attribution_video_ids(session: Session, limit: int) -> list[int]:
    """Multi-guest channel videos with instrument mentions but no participants row yet."""
    from src.channels.models import YouTubeChannel

    channels = session.exec(select(YouTubeChannel)).all()
    multi = [c.id for c in channels if detector.is_multi_guest_channel(c.channel_handle)]
    if not multi:
        return []
    stmt = (
        select(Video.id, func.max(Video.published_at))
        .join(TopicMention, TopicMention.video_id == Video.id)
        .join(Topic, TopicMention.topic_id == Topic.id)
        .outerjoin(VideoParticipants, VideoParticipants.video_id == Video.id)
        .where(Video.channel_id.in_(multi))
        .where(Topic.slug.in_(list(INSTRUMENTS)))
        .where(
            (VideoParticipants.video_id.is_(None))
            | (VideoParticipants.version != PARTICIPANTS_PROMPT_VERSION)
        )
        .group_by(Video.id)
        .order_by(func.max(Video.published_at).desc().nulls_last(), Video.id.desc())
    )
    return [row[0] for row in session.exec(stmt.limit(limit)).all()]


def coverage(session: Session) -> dict:
    base = (
        select(func.count(TopicMention.id))
        .join(Topic, TopicMention.topic_id == Topic.id)
        .where(Topic.slug.in_(list(INSTRUMENTS)))
    )
    total = session.exec(base).one()
    on_version = TopicMention.outlook_version == OUTLOOK_PROMPT_VERSION
    current = session.exec(base.where(on_version)).one()
    explicit = session.exec(
        base.where(on_version).where(TopicMention.outlook_explicit.is_(True))
    ).one()
    return {"instrument_mentions": total, "classified": current, "explicit": explicit}


def attribution_coverage(session: Session) -> dict:
    """Multi-guest channel videos with / without detected participants (OI-018)."""
    attributed = session.exec(
        select(func.count(VideoParticipants.video_id)).where(
            VideoParticipants.version == PARTICIPANTS_PROMPT_VERSION
        )
    ).one()
    return {
        "multi_guest_videos_attributed": attributed,
        "multi_guest_videos_pending": len(pending_attribution_video_ids(session, 100000)),
    }


_state_lock = threading.Lock()
_state: dict = {"running": False}


def backfill_state() -> dict:
    with _state_lock:
        return dict(_state)


def run_backfill(engine, *, limit: int, workers: int = 3) -> None:
    """Classify up to `limit` pending videos with a small worker pool. Single-flight."""
    with _state_lock:
        if _state.get("running"):
            return
        _state.clear()
        _state.update(
            running=True,
            started_at=datetime.now(UTC).isoformat(),
            version=OUTLOOK_PROMPT_VERSION,
            queued=0,
            done=0,
            failed=0,
            last_error=None,
        )
    try:
        with Session(engine) as session:
            assign_channel_default_persons(session)
            clear_unattributed_multi_guest_persons(session)
            first = pending_attribution_video_ids(session, limit)
            rest = [i for i in pending_video_ids(session, limit) if i not in set(first)]
            ids = (first + rest)[:limit]
        with _state_lock:
            _state["queued"] = len(ids)

        def one(video_id: int) -> None:
            with Session(engine) as session:
                try:
                    video = session.get(Video, video_id)
                    if video is not None:
                        classify_video(session, video)
                    with _state_lock:
                        _state["done"] += 1
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    logger.warning("Outlook backfill failed video_id=%s", video_id, exc_info=True)
                    with _state_lock:
                        _state["failed"] += 1
                        _state["last_error"] = f"video {video_id}: {str(exc)[:200]}"

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, ids))
    finally:
        with _state_lock:
            _state["running"] = False
            _state["finished_at"] = datetime.now(UTC).isoformat()



_clean_lock = threading.Lock()
_clean_state: dict = {"running": False}


def clean_quote_state() -> dict:
    with _clean_lock:
        return dict(_clean_state)


def clean_quote_coverage(session: Session) -> dict:
    with_quote = select(func.count(TopicMention.id)).where(TopicMention.outlook_quote.is_not(None))
    return {
        "quotes": session.exec(with_quote).one(),
        "cleaned": session.exec(
            with_quote.where(TopicMention.outlook_quote_clean.is_not(None))
        ).one(),
    }


def run_clean_quote_backfill(engine, *, limit: int) -> None:
    """Clean up to `limit` stored quotes that have no clean version yet. Single-flight."""
    with _clean_lock:
        if _clean_state.get("running"):
            return
        _clean_state.clear()
        _clean_state.update(
            running=True, started_at=datetime.now(UTC).isoformat(), queued=0, done=0, failed=0,
            last_error=None,
        )
    try:
        with Session(engine) as session:
            mentions = session.exec(
                select(TopicMention, Topic)
                .join(Topic, TopicMention.topic_id == Topic.id)
                .where(TopicMention.outlook_quote.is_not(None))
                .where(TopicMention.outlook_quote_clean.is_(None))
                .order_by(TopicMention.id.desc())
                .limit(limit)
            ).all()
            with _clean_lock:
                _clean_state["queued"] = len(mentions)
            segments_by_video: dict[int, list] = {}
            items: list[tuple[TopicMention, str, str]] = []
            for mention, topic in mentions:
                if mention.video_id not in segments_by_video:
                    transcript = session.exec(
                        select(Transcript).where(Transcript.video_id == mention.video_id)
                    ).first()
                    segments_by_video[mention.video_id] = (
                        videos_service.parse_transcript_segments(transcript.segments_json)
                        if transcript
                        else []
                    )
                excerpt = transcript_excerpt(
                    segments_by_video[mention.video_id], mention.start_time, mention.end_time
                )
                items.append((mention, INSTRUMENTS.get(topic.slug, topic.name), excerpt))

            for start in range(0, len(items), quote_clean.BATCH):
                chunk = items[start : start + quote_clean.BATCH]
                results = {m.id: {"outlook_quote": m.outlook_quote} for m, _, _ in chunk}
                add_clean_quotes(results, chunk)
                stored = 0
                for mention, _, _ in chunk:
                    value = results[mention.id]["outlook_quote_clean"]
                    if value is not None:
                        mention.outlook_quote_clean = value
                        session.add(mention)
                        stored += 1
                session.commit()
                with _clean_lock:
                    _clean_state["done"] += stored
                    _clean_state["failed"] += len(chunk) - stored
                    if stored < len(chunk):
                        _clean_state["last_error"] = f"LLM failed for chunk at {start}"
    finally:
        with _clean_lock:
            _clean_state["running"] = False
            _clean_state["finished_at"] = datetime.now(UTC).isoformat()


def assign_channel_default_persons(session: Session) -> int:
    """Single-host channels added to channel_defaults later (Cuma Cevik, Yatırım 101):
    give their existing videos and mentions the channel's person. No LLM call."""
    from src.channels.models import YouTubeChannel
    from src.persons import service as persons_service

    updated = 0
    for channel in session.exec(select(YouTubeChannel)).all():
        if detector.is_multi_guest_channel(channel.channel_handle):
            continue
        slug = detector.detect_channel_default(channel.channel_handle)
        person = persons_service.upsert_known_person(session, slug) if slug else None
        if person is None:
            continue
        videos = session.exec(
            select(Video).where(Video.channel_id == channel.id).where(Video.person_id.is_(None))
        ).all()
        for video in videos:
            video.person_id = person.id
            session.add(video)
            for mention in session.exec(
                select(TopicMention)
                .where(TopicMention.video_id == video.id)
                .where(TopicMention.person_id.is_(None))
            ).all():
                mention.person_id = person.id
                session.add(mention)
            updated += 1
    session.commit()
    return updated


def clear_unattributed_multi_guest_persons(session: Session) -> int:
    """Drop the headline-guest person from mentions of multi-guest videos that have
    not been attributed yet, so no view sits under the wrong name meanwhile."""
    from src.channels.models import YouTubeChannel

    multi = [
        c.id for c in session.exec(select(YouTubeChannel)).all()
        if detector.is_multi_guest_channel(c.channel_handle)
    ]
    if not multi:
        return 0
    attributed = select(VideoParticipants.video_id)
    mentions = session.exec(
        select(TopicMention)
        .where(TopicMention.channel_id.in_(multi))
        .where(TopicMention.person_id.is_not(None))
        .where(TopicMention.video_id.not_in(attributed))
    ).all()
    for mention in mentions:
        mention.person_id = None
        session.add(mention)
    session.commit()
    return len(mentions)
