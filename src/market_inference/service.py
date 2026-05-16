"""Market inference service — collects weighted video sources and calls LLM per topic."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select, text

from src.channels.models import YouTubeChannel
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.llm.service import LLMGenerationError
from src.market_inference.constants import (
    INFERENCE_TOPIC_LABELS,
    INFERENCE_TOPIC_MAP,
    MIN_CONFIDENCE_THRESHOLD,
    TOP_K_SOURCES,
    VALID_DIRECTIONS,
)
from src.market_inference.models import (
    MarketInferenceRun,
    MarketInferenceSource,
    MarketInferenceTopic,
)
from src.market_inference.weights import is_included, resolve_weight
from src.persons.models import Person
from src.topics.models import Topic
from src.videos.models import Video

logger = logging.getLogger(__name__)

_COLD_START_HOURS = 168  # 7 days — ensures first run has enough data


@dataclass
class _SourceItem:
    video_id: int
    title: str | None
    channel_handle: str | None
    channel_name: str | None
    person_slug: str | None
    person_name: str | None
    weight: float
    sentiment: str | None
    key_levels: list[str]
    mention_summary: str
    published_at: datetime | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _recency_score(published_at: datetime | None, now: datetime) -> float:
    if not published_at:
        return 0.5
    age_hours = max(0, (now - published_at).total_seconds() / 3600)
    # Linear decay: full score at 0h, half at 48h, ~0 at 96h
    return max(0.0, 1.0 - age_hours / 96.0)


def _get_topic_ids(session: Session, slugs: list[str]) -> list[int]:
    rows = session.exec(select(Topic).where(Topic.slug.in_(slugs))).all()  # type: ignore[attr-defined]
    return [r.id for r in rows if r.id is not None]


def _collect_sources(
    session: Session,
    topic_key: str,
    window_start: datetime,
    window_end: datetime,
) -> list[_SourceItem]:
    slugs = INFERENCE_TOPIC_MAP.get(topic_key, [])
    if not slugs:
        return []
    topic_ids = _get_topic_ids(session, slugs)
    if not topic_ids:
        return []

    now = _utc_now()

    rows = session.exec(
        select(TopicMention, Video, YouTubeChannel, Person)
        .join(Video, TopicMention.video_id == Video.id)  # type: ignore[arg-type]
        .join(YouTubeChannel, Video.channel_id == YouTubeChannel.id, isouter=True)  # type: ignore[arg-type]
        .join(Person, Video.person_id == Person.id, isouter=True)  # type: ignore[arg-type]
        .where(TopicMention.topic_id.in_(topic_ids))  # type: ignore[attr-defined]
        .where(TopicMention.confidence >= MIN_CONFIDENCE_THRESHOLD)
        .where(
            # Use published_at when available, fall back to created_at
            (Video.published_at >= window_start) | (  # type: ignore[operator]
                (Video.published_at.is_(None)) & (Video.created_at >= window_start)  # type: ignore[attr-defined]
            )
        )
        .where(
            (Video.published_at <= window_end) | (  # type: ignore[operator]
                (Video.published_at.is_(None)) & (Video.created_at <= window_end)  # type: ignore[attr-defined]
            )
        )
    ).all()

    items: list[_SourceItem] = []
    seen_video_ids: set[int] = set()

    for mention, video, channel, person in rows:
        if video.id in seen_video_ids:
            continue

        channel_handle = channel.channel_handle if channel else None
        person_slug = person.slug if person else None

        weight = resolve_weight(channel_handle, person_slug, topic_key)
        if not is_included(channel_handle, person_slug, topic_key):
            continue

        key_levels: list[str] = []
        if mention.key_levels:
            try:
                key_levels = json.loads(mention.key_levels)
            except Exception:
                pass

        score = weight * mention.confidence * _recency_score(video.published_at, now)
        items.append(
            _SourceItem(
                video_id=video.id,
                title=video.title,
                channel_handle=channel_handle,
                channel_name=channel.name if channel else None,
                person_slug=person_slug,
                person_name=person.name if person else None,
                weight=weight,
                sentiment=mention.sentiment,
                key_levels=key_levels,
                mention_summary=mention.summary or "",
                published_at=video.published_at,
            )
        )
        seen_video_ids.add(video.id)
        items[-1]._score = score  # type: ignore[attr-defined]

    items.sort(key=lambda x: getattr(x, "_score", 0), reverse=True)
    return items[:TOP_K_SOURCES]


def _build_sources_block(sources: list[_SourceItem]) -> str:
    if not sources:
        return "(Kaynak bulunamadı)"
    lines = []
    for i, src in enumerate(sources, 1):
        channel = src.channel_name or src.channel_handle or "?"
        person = src.person_name or "-"
        title = src.title or "(başlık yok)"
        sentiment = src.sentiment or "neutral"
        kl = ", ".join(src.key_levels) if src.key_levels else "-"
        pub = src.published_at.strftime("%Y-%m-%d") if src.published_at else "?"
        lines.append(
            f"[{i}] [agirlik={src.weight:.1f}] {person} / {channel} — {title!r}\n"
            f"    Tarih: {pub} | Duygu: {sentiment} | Önemli seviyeler: {kl}\n"
            f"    Özet: {src.mention_summary}\n"
            f"    video_id: {src.video_id}"
        )
    return "\n\n".join(lines)


def _build_prev_section(prior: MarketInferenceTopic | None) -> str:
    if not prior:
        return "(Önceki çıkarım yok — ilk değerlendirme.)"
    tags = ""
    if prior.tags:
        try:
            tags = ", ".join(json.loads(prior.tags))
        except Exception:
            tags = prior.tags
    return (
        f"ÖNCEKİ ÇIKARIM\n"
        f"==============\n"
        f"Yön        : {prior.direction}\n"
        f"Güven      : {prior.confidence:.2f}\n"
        f"Özet       : {prior.summary}\n"
        f"Etiketler  : {tags or '-'}"
    )


def _get_prior_run(session: Session) -> MarketInferenceRun | None:
    return session.exec(
        select(MarketInferenceRun)
        .where(MarketInferenceRun.status.in_(["success", "partial_fail"]))  # type: ignore[attr-defined]
        .order_by(MarketInferenceRun.id.desc())  # type: ignore[attr-defined]
        .limit(1)
    ).first()


def _get_prior_topic(
    session: Session, run: MarketInferenceRun, topic_key: str
) -> MarketInferenceTopic | None:
    return session.exec(
        select(MarketInferenceTopic)
        .where(MarketInferenceTopic.run_id == run.id)
        .where(MarketInferenceTopic.topic_key == topic_key)
        .limit(1)
    ).first()


def _parse_inference_payload(
    payload: dict,
    prior: MarketInferenceTopic | None,
    sources: list[_SourceItem],
    run_id: int,
    topic_key: str,
) -> tuple[MarketInferenceTopic, list[MarketInferenceSource]]:
    direction = str(payload.get("direction", "mixed"))
    if direction not in VALID_DIRECTIONS:
        direction = "mixed"
    confidence = float(payload.get("confidence", 0.5))
    summary = str(payload.get("summary", ""))
    tags_raw = payload.get("tags", [])
    tags = json.dumps(tags_raw, ensure_ascii=False) if isinstance(tags_raw, list) else None
    changed = bool(payload.get("changed_from_prev", False))
    change_reason = payload.get("change_reason") or None
    if isinstance(change_reason, str) and not change_reason.strip():
        change_reason = None

    status = "updated" if (prior and changed) else ("new" if not prior else "carried_over")
    if prior and changed:
        status = "updated"
    elif not prior:
        status = "new"
    else:
        status = "carried_over" if not changed else "updated"

    topic_row = MarketInferenceTopic(
        run_id=run_id,
        topic_key=topic_key,
        direction=direction,
        confidence=confidence,
        summary=summary,
        tags=tags,
        status=status,
        changed_from_prev=changed,
        change_reason=change_reason,
    )

    source_rows: list[MarketInferenceSource] = []
    source_map = {s.video_id: s for s in sources}
    for contrib in payload.get("contributions", []):
        vid_id = contrib.get("video_id")
        note = contrib.get("note") or None
        if vid_id and vid_id in source_map:
            source_rows.append(
                MarketInferenceSource(
                    run_id=run_id,
                    topic_key=topic_key,
                    video_id=vid_id,
                    weight_used=source_map[vid_id].weight,
                    contribution_note=str(note)[:500] if note else None,
                )
            )

    return topic_row, source_rows


def _carry_over(
    prior: MarketInferenceTopic,
    run_id: int,
    topic_key: str,
) -> MarketInferenceTopic:
    return MarketInferenceTopic(
        run_id=run_id,
        topic_key=topic_key,
        direction=prior.direction,
        confidence=prior.confidence,
        summary=prior.summary,
        tags=prior.tags,
        status="carried_over",
        changed_from_prev=False,
        change_reason=None,
    )


def run_once(engine: Engine) -> dict:
    """Execute one inference cycle. Idempotent: skips if today already has a run."""
    from src.config import settings  # noqa: F401

    now = _utc_now()
    run_date = now.strftime("%Y-%m-%d")

    with Session(engine) as session:
        existing = session.exec(
            select(MarketInferenceRun).where(MarketInferenceRun.run_date == run_date)
        ).first()
        if existing and existing.status in ("success", "skipped_no_new_videos"):
            logger.info("Market inference already completed for %s (status=%s)", run_date, existing.status)
            return {"status": existing.status, "run_date": run_date, "run_id": existing.id}

        prior_run = _get_prior_run(session)
        window_start: datetime
        if prior_run and prior_run.window_end:
            window_start = prior_run.window_end
        else:
            window_start = now - timedelta(hours=_COLD_START_HOURS)
        window_end = now

        # Check if there are any new videos in scope before creating a run record.
        new_video_count = session.exec(
            text(
                "SELECT COUNT(*) FROM video WHERE published_at >= :ws AND published_at <= :we"
            ),
            params={"ws": window_start, "we": window_end},
        ).one()[0]

        if new_video_count == 0 and prior_run is not None:
            run = MarketInferenceRun(
                run_date=run_date,
                status="skipped_no_new_videos",
                window_start=window_start,
                window_end=window_end,
                finished_at=now,
            )
            session.add(run)
            session.commit()
            logger.info("No new videos since last run — skipping inference for %s", run_date)
            return {"status": "skipped_no_new_videos", "run_date": run_date}

        run = MarketInferenceRun(
            run_date=run_date,
            status="running",
            window_start=window_start,
            window_end=window_end,
            model=settings.MINIMAX_MODEL,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    topic_statuses: dict[str, str] = {}
    errors: list[str] = []

    for topic_key in INFERENCE_TOPIC_MAP:
        label = INFERENCE_TOPIC_LABELS.get(topic_key, topic_key)
        logger.info("Processing topic: %s (%s)", topic_key, label)

        try:
            with Session(engine) as session:
                sources = _collect_sources(session, topic_key, window_start, window_end)
                prior_run_obj = _get_prior_run(session)
                prior = _get_prior_topic(session, prior_run_obj, topic_key) if prior_run_obj else None

                if not sources:
                    if prior:
                        topic_row = _carry_over(prior, run_id, topic_key)
                        session.add(topic_row)
                        session.commit()
                        topic_statuses[topic_key] = "carried_over"
                    else:
                        topic_statuses[topic_key] = "no_data"
                    continue

                sources_block = _build_sources_block(sources)
                prev_section = _build_prev_section(prior)

                payload = llm_service.generate_market_inference_json(
                    topic_key=topic_key,
                    topic_label=label,
                    sources_block=sources_block,
                    prev_section=prev_section,
                )

                topic_row, source_rows = _parse_inference_payload(
                    payload, prior, sources, run_id, topic_key
                )
                session.add(topic_row)
                for sr in source_rows:
                    session.add(sr)
                session.commit()
                topic_statuses[topic_key] = topic_row.status
                logger.info(
                    "Topic %s → direction=%s confidence=%.2f status=%s",
                    topic_key,
                    topic_row.direction,
                    topic_row.confidence,
                    topic_row.status,
                )

        except LLMGenerationError as exc:
            logger.error("LLM error for topic %s: %s", topic_key, exc)
            errors.append(f"{topic_key}: {exc}")
            topic_statuses[topic_key] = "error"
            with Session(engine) as session:
                prior_run_obj = _get_prior_run(session)
                prior = _get_prior_topic(session, prior_run_obj, topic_key) if prior_run_obj else None
                if prior:
                    topic_row = _carry_over(prior, run_id, topic_key)
                    topic_row.status = "error"
                    session.add(topic_row)
                    session.commit()

        except Exception as exc:
            logger.error("Unexpected error for topic %s: %s", topic_key, exc, exc_info=True)
            errors.append(f"{topic_key}: {exc}")
            topic_statuses[topic_key] = "error"

    error_count = sum(1 for s in topic_statuses.values() if s == "error")
    no_data_count = sum(1 for s in topic_statuses.values() if s == "no_data")
    total = len(INFERENCE_TOPIC_MAP)

    if error_count == 0:
        final_status = "success"
    elif error_count < total:
        final_status = "partial_fail"
    else:
        final_status = "failed"

    with Session(engine) as session:
        run_obj = session.get(MarketInferenceRun, run_id)
        if run_obj:
            run_obj.status = final_status
            run_obj.finished_at = _utc_now()
            run_obj.window_end = window_end
            if errors:
                run_obj.error = "; ".join(errors[:3])
            session.add(run_obj)
            session.commit()

    logger.info(
        "Inference run %d finished — status=%s topics=%d errors=%d no_data=%d",
        run_id,
        final_status,
        total,
        error_count,
        no_data_count,
    )

    return {
        "status": final_status,
        "run_id": run_id,
        "run_date": run_date,
        "topic_statuses": topic_statuses,
    }
