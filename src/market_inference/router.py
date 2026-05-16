from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlmodel import Session, select

from src import cache
from src.channels.models import YouTubeChannel
from src.database import SessionDep, engine
from src.market_inference.constants import INFERENCE_TOPIC_LABELS, INFERENCE_TOPIC_MAP
from src.market_inference.models import (
    MarketInferenceRun,
    MarketInferenceSource,
    MarketInferenceTopic,
)
from src.market_inference.schemas import (
    InferenceRunOut,
    InferenceRunSummary,
    InferenceSourceOut,
    InferenceTopicHistory,
    InferenceTopicOut,
)
from src.videos.models import Video
from src.persons.models import Person

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/inference", tags=["market-inference"])

_CACHE_KEY_LATEST = "inference:latest"


def _build_topic_out(
    session: Session,
    topic: MarketInferenceTopic,
    run_id: int,
) -> InferenceTopicOut:
    sources = session.exec(
        select(MarketInferenceSource)
        .where(MarketInferenceSource.run_id == run_id)
        .where(MarketInferenceSource.topic_key == topic.topic_key)
    ).all()

    source_outs: list[InferenceSourceOut] = []
    for src in sources:
        video = session.get(Video, src.video_id)
        channel = session.get(YouTubeChannel, video.channel_id) if video and video.channel_id else None
        person = session.get(Person, video.person_id) if video and video.person_id else None
        source_outs.append(
            InferenceSourceOut(
                video_id=src.video_id,
                title=video.title if video else None,
                channel_name=channel.name if channel else None,
                person_name=person.name if person else None,
                weight_used=src.weight_used,
                contribution_note=src.contribution_note,
            )
        )

    return InferenceTopicOut(
        topic_key=topic.topic_key,
        topic_label=INFERENCE_TOPIC_LABELS.get(topic.topic_key, topic.topic_key),
        direction=topic.direction,
        confidence=topic.confidence,
        summary=topic.summary,
        tags=topic.tags,  # type: ignore[arg-type]
        status=topic.status,
        changed_from_prev=topic.changed_from_prev,
        change_reason=topic.change_reason,
        sources=source_outs,
    )


def _build_run_out(session: Session, run: MarketInferenceRun) -> InferenceRunOut:
    topics = session.exec(
        select(MarketInferenceTopic).where(MarketInferenceTopic.run_id == run.id)
    ).all()

    topic_outs = [_build_topic_out(session, t, run.id) for t in topics]

    return InferenceRunOut(
        run_id=run.id,
        run_date=run.run_date,
        status=run.status,
        generated_at=run.finished_at,
        window_start=run.window_start,
        window_end=run.window_end,
        topics=topic_outs,
    )


@router.get(
    "/latest",
    response_model=InferenceRunOut,
    summary="Latest market inference run with all topic outlooks",
)
def get_latest_inference(session: SessionDep) -> InferenceRunOut:
    cached = cache.get_cached(_CACHE_KEY_LATEST)
    if cached:
        try:
            return InferenceRunOut.model_validate(cached)
        except Exception:
            pass

    run = session.exec(
        select(MarketInferenceRun)
        .where(MarketInferenceRun.status.in_(["success", "partial_fail"]))  # type: ignore[attr-defined]
        .order_by(MarketInferenceRun.id.desc())  # type: ignore[attr-defined]
        .limit(1)
    ).first()

    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No inference run found.")

    out = _build_run_out(session, run)
    cache.set_cached(_CACHE_KEY_LATEST, out.model_dump(mode="json"))
    return out


@router.get(
    "/runs",
    response_model=list[InferenceRunSummary],
    summary="List historical inference runs",
)
def list_runs(
    session: SessionDep,
    limit: int = Query(default=30, ge=1, le=365),
) -> list[InferenceRunSummary]:
    runs = session.exec(
        select(MarketInferenceRun)
        .order_by(MarketInferenceRun.id.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [
        InferenceRunSummary(
            run_id=r.id,
            run_date=r.run_date,
            status=r.status,
            finished_at=r.finished_at,
        )
        for r in runs
    ]


@router.get(
    "/runs/{run_id}",
    response_model=InferenceRunOut,
    summary="Full detail for a specific inference run",
)
def get_run(run_id: int, session: SessionDep) -> InferenceRunOut:
    run = session.get(MarketInferenceRun, run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found.")
    return _build_run_out(session, run)


@router.get(
    "/topics/{topic_key}/history",
    response_model=list[InferenceTopicHistory],
    summary="Historical direction trend for a single topic",
)
def get_topic_history(
    topic_key: str,
    session: SessionDep,
    days: int = Query(default=30, ge=1, le=365),
) -> list[InferenceTopicHistory]:
    if topic_key not in INFERENCE_TOPIC_MAP:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown topic_key. Valid keys: {list(INFERENCE_TOPIC_MAP.keys())}",
        )

    rows = session.exec(
        select(MarketInferenceTopic, MarketInferenceRun)
        .join(MarketInferenceRun, MarketInferenceTopic.run_id == MarketInferenceRun.id)  # type: ignore[arg-type]
        .where(MarketInferenceTopic.topic_key == topic_key)
        .where(MarketInferenceRun.status.in_(["success", "partial_fail"]))  # type: ignore[attr-defined]
        .order_by(MarketInferenceRun.id.desc())  # type: ignore[attr-defined]
        .limit(days)
    ).all()

    return [
        InferenceTopicHistory(
            run_date=run.run_date,
            direction=topic.direction,
            confidence=topic.confidence,
            changed_from_prev=topic.changed_from_prev,
        )
        for topic, run in rows
    ]


def _trigger_background(run_in_background: bool = False) -> dict:
    from src.market_inference import service

    result = service.run_once(engine)
    if result.get("status") in ("success", "skipped_no_new_videos", "partial_fail"):
        cache.invalidate(_CACHE_KEY_LATEST)
    return result


@router.post(
    "/runs:trigger",
    summary="Manually trigger an inference run (idempotent — skips if today already done)",
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_run(background_tasks: BackgroundTasks) -> dict:
    background_tasks.add_task(_trigger_background)
    return {"status": "accepted", "message": "Inference run started in background."}
