"""Read-only HTTP access to YouTube watch job history."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from src.database import get_session
from src.jobs.youtube_watch.models import YouTubeWatchChannelResult, YouTubeWatchRun

router = APIRouter(prefix="/jobs/youtube-watch", tags=["jobs"])


def _serialize_run(run: YouTubeWatchRun) -> dict:
    error_details: list[str] | None = None
    if run.error_details:
        try:
            parsed = json.loads(run.error_details)
            if isinstance(parsed, list):
                error_details = [str(x) for x in parsed]
            else:
                error_details = [str(parsed)]
        except json.JSONDecodeError:
            error_details = [run.error_details]

    duration_seconds: float | None = None
    if run.finished_at and run.started_at:
        duration_seconds = (run.finished_at - run.started_at).total_seconds()

    return {
        "id": run.id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_seconds": duration_seconds,
        "status": run.status,
        "channels_scanned": run.channels_scanned,
        "videos_detected": run.videos_detected,
        "videos_ingested": run.videos_ingested,
        "videos_skipped": run.videos_skipped,
        "errors_count": run.errors_count,
        "error_details": error_details,
    }


def _serialize_channel_result(row: YouTubeWatchChannelResult) -> dict:
    return {
        "id": row.id,
        "channel_identifier": row.channel_identifier,
        "channel_name": row.channel_name,
        "resolved_channel_id": row.resolved_channel_id,
        "videos_detected": row.videos_detected,
        "videos_ingested": row.videos_ingested,
        "videos_skipped": row.videos_skipped,
        "errors_count": row.errors_count,
        "status": row.status,
        "error_detail": row.error_detail,
    }


@router.get("/runs")
def list_runs(
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    rows = session.exec(
        select(YouTubeWatchRun).order_by(YouTubeWatchRun.id.desc()).limit(limit)
    ).all()
    return {"count": len(rows), "runs": [_serialize_run(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)) -> dict:
    run = session.get(YouTubeWatchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    channels = session.exec(
        select(YouTubeWatchChannelResult)
        .where(YouTubeWatchChannelResult.run_id == run_id)
        .order_by(YouTubeWatchChannelResult.id.asc())
    ).all()
    payload = _serialize_run(run)
    payload["channels"] = [_serialize_channel_result(c) for c in channels]
    return payload
