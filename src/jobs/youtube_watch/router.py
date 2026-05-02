"""Read-only HTTP access to YouTube watch job history."""

import json
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, select

from src.classification.models import TopicMention
from src.config import settings
from src.database import get_session
from src.jobs.youtube_watch.config import load_watch_config
from src.jobs.youtube_watch.models import YouTubeWatchChannelResult, YouTubeWatchRun
from src.models import utc_now
from src.videos.models import Transcript, Video, VideoSummary

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


@router.get(
    "/health",
    summary="Pipeline health: latest run, 24h aggregate, stuck videos, config",
)
def pipeline_health(session: Session = Depends(get_session)) -> dict:
    """Single-shot diagnostic for the YouTube watch pipeline.

    Combines: the latest run, 24h success/error aggregates, the channel-level
    failures from recent runs, the count of stuck videos broken down by reason,
    and the relevant config flags (LLM configured, proxy enabled, watched
    channels). Use this when the iOS app reports missing/un-summarized videos.
    """
    now = utc_now()
    since_24h = now - timedelta(hours=24)

    latest_run = session.exec(
        select(YouTubeWatchRun).order_by(YouTubeWatchRun.id.desc()).limit(1)
    ).first()

    # ── 24h aggregate ─────────────────────────────────────────────────────────
    runs_24h = list(
        session.exec(
            select(YouTubeWatchRun)
            .where(YouTubeWatchRun.started_at >= since_24h)
            .order_by(YouTubeWatchRun.id.desc())
        ).all()
    )
    status_counts = Counter(r.status for r in runs_24h)
    last_24h = {
        "runs_total": len(runs_24h),
        "runs_success": status_counts.get("success", 0),
        "runs_partial": status_counts.get("partial_fail", 0),
        "runs_failed": status_counts.get("failed", 0),
        "videos_ingested": sum(r.videos_ingested for r in runs_24h),
        "videos_detected": sum(r.videos_detected for r in runs_24h),
        "videos_skipped": sum(r.videos_skipped for r in runs_24h),
        "errors_count": sum(r.errors_count for r in runs_24h),
        "first_run_at": runs_24h[-1].started_at if runs_24h else None,
        "last_run_at": runs_24h[0].started_at if runs_24h else None,
    }

    # ── Recent channel-level failures (last 12 runs with errors) ──────────────
    recent_run_ids_with_errors = [r.id for r in runs_24h if r.errors_count > 0][:12]
    recent_failures: list[dict] = []
    if recent_run_ids_with_errors:
        failed_channels = session.exec(
            select(YouTubeWatchChannelResult)
            .where(YouTubeWatchChannelResult.run_id.in_(recent_run_ids_with_errors))
            .where(YouTubeWatchChannelResult.errors_count > 0)
            .order_by(YouTubeWatchChannelResult.run_id.desc())
        ).all()
        run_started_by_id = {r.id: r.started_at for r in runs_24h}
        for row in failed_channels:
            recent_failures.append(
                {
                    "run_id": row.run_id,
                    "run_started_at": run_started_by_id.get(row.run_id),
                    "channel_name": row.channel_name,
                    "channel_identifier": row.channel_identifier,
                    "errors_count": row.errors_count,
                    "status": row.status,
                    "error_detail": row.error_detail,
                }
            )

    # ── Stuck videos: anything with incomplete pipeline state ─────────────────
    transcript_exists = (
        select(Transcript.id).where(Transcript.video_id == Video.id).exists()
    )
    summary_exists = (
        select(VideoSummary.id).where(VideoSummary.video_id == Video.id).exists()
    )
    classification_exists = (
        select(TopicMention.id).where(TopicMention.video_id == Video.id).exists()
    )

    missing_transcript = session.exec(
        select(func.count())
        .select_from(Video)
        .where(~transcript_exists)
        .where(Video.transcript_status != "video_unavailable")
    ).one()
    missing_summary = session.exec(
        select(func.count())
        .select_from(Video)
        .where(transcript_exists)
        .where(~summary_exists)
    ).one()
    missing_classification = session.exec(
        select(func.count())
        .select_from(Video)
        .where(transcript_exists)
        .where(~classification_exists)
    ).one()

    null_published_at = session.exec(
        select(func.count()).select_from(Video).where(Video.published_at.is_(None))
    ).one()

    transcript_status_counts = dict(
        session.exec(
            select(Video.transcript_status, func.count())
            .group_by(Video.transcript_status)
            .order_by(func.count().desc())
        ).all()
    )

    error_code_counts = dict(
        session.exec(
            select(Video.transcript_last_error_code, func.count())
            .where(Video.transcript_last_error_code.is_not(None))
            .group_by(Video.transcript_last_error_code)
            .order_by(func.count().desc())
        ).all()
    )

    # Sample of the oldest stuck videos so the user can spot-check what's wrong.
    stuck_sample_rows = list(
        session.exec(
            select(
                Video.id,
                Video.video_id,
                Video.video_url,
                Video.title,
                Video.transcript_status,
                Video.transcript_attempt_count,
                Video.transcript_last_error_code,
                Video.transcript_last_error_detail,
                Video.transcript_next_retry_at,
                Video.created_at,
            )
            .where(
                (~transcript_exists)
                | (~summary_exists)
                | (~classification_exists)
            )
            .where(Video.transcript_status != "video_unavailable")
            .order_by(Video.created_at.asc())
            .limit(10)
        ).all()
    )
    stuck_samples: list[dict] = []
    for row in stuck_sample_rows:
        has_transcript = (
            session.exec(select(Transcript.id).where(Transcript.video_id == row[0])).first()
            is not None
        )
        has_summary = (
            session.exec(select(VideoSummary.id).where(VideoSummary.video_id == row[0])).first()
            is not None
        )
        if has_transcript and has_summary:
            issue = "missing_classification"
        elif has_transcript:
            issue = "missing_summary"
        else:
            issue = "missing_transcript"
        stuck_samples.append(
            {
                "video_id": row[0],
                "youtube_video_id": row[1],
                "video_url": row[2],
                "title": row[3],
                "issue": issue,
                "transcript_status": row[4],
                "transcript_attempt_count": row[5],
                "transcript_last_error_code": row[6],
                "transcript_last_error_detail": row[7],
                "transcript_next_retry_at": row[8],
                "created_at": row[9],
            }
        )

    # ── Pipeline config flags ─────────────────────────────────────────────────
    try:
        watched_count = len(load_watch_config().channels)
    except Exception:  # config file missing or malformed should not break /health
        watched_count = None

    config_flags = {
        "environment": settings.ENVIRONMENT,
        "llm_configured": bool(settings.MINIMAX_BASE_URL and settings.MINIMAX_API_KEY),
        "youtube_proxy_enabled": bool(settings.YOUTUBE_PROXY_ENABLED),
        "youtube_proxy_mode": settings.YOUTUBE_PROXY_MODE,
        "webshare_api_key_set": bool(settings.WEBSHARE_API_KEY),
        "watched_channels_count": watched_count,
    }

    # ── Top-level health verdict ──────────────────────────────────────────────
    if latest_run is None:
        verdict = "no_runs"
    elif latest_run.status == "running":
        verdict = "running"
    elif latest_run.status == "failed":
        verdict = "degraded"
    elif last_24h["runs_failed"] > 0:
        verdict = "degraded"
    elif last_24h["runs_partial"] > 0 or missing_summary > 0 or missing_transcript > 0:
        verdict = "ok_with_stuck_videos"
    else:
        verdict = "healthy"

    return {
        "checked_at": now,
        "verdict": verdict,
        "latest_run": _serialize_run(latest_run) if latest_run else None,
        "last_24h": last_24h,
        "recent_failures": recent_failures,
        "stuck_videos": {
            "missing_transcript": int(missing_transcript),
            "missing_summary_with_transcript": int(missing_summary),
            "missing_classification_with_transcript": int(missing_classification),
            "null_published_at": int(null_published_at),
            "by_transcript_status": transcript_status_counts,
            "by_last_error_code": error_code_counts,
            "oldest_samples": stuck_samples,
        },
        "config": config_flags,
    }
