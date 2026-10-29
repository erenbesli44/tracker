"""Fill topics the analysis missed when it only saw part of the transcript.

Re-runs the analysis call on the full transcript of recent videos and adds a
mention only for topics the video does not have yet. Existing mentions (their
outlook, cleaned quote, person) and the video summary are never changed. The
outlook step then runs for the new mentions only.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from src.classification import outlook
from src.classification import service as classification_service
from src.classification.models import TopicMention
from src.ingestion import service as ingestion_service
from src.llm import service as llm_service
from src.models import utc_now
from src.topics.models import Topic
from src.videos import service as videos_service
from src.videos.models import Transcript, Video

logger = logging.getLogger(__name__)


def candidate_video_ids(session: Session, days: int, limit: int) -> list[int]:
    """Recent videos with a transcript and at least one mention, newest first.

    Videos without any mention are left to the ingestion pipeline, which retries them.
    """
    when = func.coalesce(Video.published_at, Video.created_at)
    stmt = (
        select(Video.id)
        .join(Transcript, Transcript.video_id == Video.id)
        .where(when >= utc_now() - timedelta(days=days))
        .where(Video.id.in_(select(TopicMention.video_id)))
        .order_by(when.desc(), Video.id.desc())
        .limit(limit)
    )
    return list(session.exec(stmt).all())


def _analysis_payload(session: Session, video: Video, transcript: Transcript) -> dict:
    channel = None
    if video.channel_id is not None:
        from src.channels.models import YouTubeChannel

        channel = session.get(YouTubeChannel, video.channel_id)
    channel_name = (channel.name if channel else "") or "unknown"
    return llm_service.generate_analysis_json(
        source_platform="youtube",
        channel_name=channel_name,
        speaker_name=channel_name,
        video_title=video.title or "unknown",
        published_at=video.published_at.isoformat() if video.published_at else "unknown",
        source_url=videos_service.canonicalize_youtube_url(video.video_url),
        transcript=ingestion_service._prepare_transcript_for_llm(
            transcript.raw_text,
            transcript_segments=videos_service.parse_transcript_segments(
                transcript.segments_json
            ),
        ),
        output_language=transcript.language,
        channel_primary_topic=ingestion_service._normalize_main_topic_slug(
            (getattr(channel, "primary_topic_slug", None) or "") if channel else ""
        ),
    )


def fill_video(session: Session, video: Video, *, dry_run: bool = False) -> list[dict]:
    """Add mentions for topics the video lacks.

    Returns [{topic, confidence}] for the mentions added (or that would be).
    """
    transcript = session.exec(select(Transcript).where(Transcript.video_id == video.id)).first()
    if transcript is None or not transcript.raw_text.strip():
        return []
    payload = _analysis_payload(session, video, transcript)
    # No 5-mention cap here: the cap is what dropped topics in the first place.
    classification = ingestion_service._classification_from_llm_payload(
        session, payload, max_mentions=None
    )
    if classification is None:
        return []

    # Read existing topics after the (slow) LLM call so a concurrent write is seen.
    existing = set(
        session.exec(select(TopicMention.topic_id).where(TopicMention.video_id == video.id)).all()
    )
    items = [m for m in classification.topic_mentions if m.topic_id not in existing]
    if not items:
        return []
    slugs = {
        t.id: t.slug
        for t in session.exec(
            select(Topic).where(Topic.id.in_([m.topic_id for m in items]))
        ).all()
    }
    added = [{"topic": slugs[m.topic_id], "confidence": m.confidence} for m in items]
    if dry_run:
        return added

    mentions = classification_service.add_mentions(session, video, items)
    session.commit()
    new_ids = {m.id for m in mentions}
    if llm_service._is_minimax_configured():
        try:
            outlook.classify_video(session, video, mention_ids=new_ids)
        except Exception:  # noqa: BLE001 — the outlook backfill picks unclassified mentions up
            session.rollback()
            logger.warning("Outlook failed for new mentions video_id=%s", video.id, exc_info=True)
    return added


_lock = threading.Lock()
_state: dict = {"running": False}


def fill_state() -> dict:
    with _lock:
        return {
            **_state,
            "new_mentions_by_topic": dict(_state.get("new_mentions_by_topic", {})),
            "videos": dict(_state.get("videos", {})),
        }


def try_start(*, days: int, dry_run: bool) -> bool:
    """Claim the single run slot; False when a run is already going."""
    with _lock:
        if _state.get("running"):
            return False
        _state.clear()
        _state.update(
            running=True,
            dry_run=dry_run,
            days=days,
            started_at=datetime.now(UTC).isoformat(),
            queued=0,
            scanned=0,
            videos_with_new_topics=0,
            new_mentions_by_topic={},
            videos={},
            failed=0,
            last_error=None,
        )
    return True


def run_fill(engine, *, days: int, limit: int, workers: int = 2, dry_run: bool = False) -> None:
    """Fill missing topics for up to `limit` recent videos. Single-flight."""
    if try_start(days=days, dry_run=dry_run):
        run_started(engine, days=days, limit=limit, workers=workers, dry_run=dry_run)


def run_started(engine, *, days: int, limit: int, workers: int, dry_run: bool) -> None:
    """Body of a run whose slot was claimed with try_start; always releases it."""
    try:
        if not llm_service._is_minimax_configured():
            with _lock:
                _state["last_error"] = "MiniMax is not configured"
            return
        with Session(engine) as session:
            ids = candidate_video_ids(session, days, limit)
        with _lock:
            _state["queued"] = len(ids)

        def one(video_id: int) -> None:
            with Session(engine) as session:
                try:
                    video = session.get(Video, video_id)
                    added = fill_video(session, video, dry_run=dry_run) if video else []
                    with _lock:
                        _state["scanned"] += 1
                        if added:
                            _state["videos_with_new_topics"] += 1
                            _state["videos"][video_id] = added
                            by_topic = _state["new_mentions_by_topic"]
                            for item in added:
                                by_topic[item["topic"]] = by_topic.get(item["topic"], 0) + 1
                except Exception as exc:  # noqa: BLE001
                    session.rollback()
                    logger.warning("Fill missing topics failed video_id=%s", video_id,
                                   exc_info=True)
                    with _lock:
                        _state["scanned"] += 1
                        _state["failed"] += 1
                        _state["last_error"] = f"video {video_id}: {str(exc)[:200]}"

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, ids))
    finally:
        with _lock:
            _state["running"] = False
            _state["finished_at"] = datetime.now(UTC).isoformat()
