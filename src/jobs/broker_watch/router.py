"""Read-only HTTP access to broker watch job history and health."""

import json
from collections import Counter
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from sqlmodel import select

from src.config import settings
from src.database import SessionDep
from src.jobs.broker_watch.config import load_broker_config
from src.jobs.broker_watch.models import BrokerWatchRun, BrokerWatchSourceResult
from src.models import utc_now

router = APIRouter(prefix="/jobs/broker-watch", tags=["jobs"])

STALE_AFTER = timedelta(days=7)


def _serialize_run(run: BrokerWatchRun) -> dict:
    error_details: list[str] | None = None
    if run.error_details:
        try:
            parsed = json.loads(run.error_details)
            error_details = [str(x) for x in parsed] if isinstance(parsed, list) else [str(parsed)]
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
        "sources_scanned": run.sources_scanned,
        "documents_new": run.documents_new,
        "events_new": run.events_new,
        "snapshots_new": run.snapshots_new,
        "errors_count": run.errors_count,
        "error_details": error_details,
    }


def _serialize_source_result(row: BrokerWatchSourceResult) -> dict:
    return {
        "id": row.id,
        "broker_slug": row.broker_slug,
        "broker_name": row.broker_name,
        "status": row.status,
        "documents_seen": row.documents_seen,
        "documents_new": row.documents_new,
        "events_new": row.events_new,
        "snapshots_new": row.snapshots_new,
        "errors_count": row.errors_count,
        "error_detail": row.error_detail,
    }


@router.get("/runs")
def list_runs(session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> dict:
    rows = session.exec(
        select(BrokerWatchRun).order_by(BrokerWatchRun.id.desc()).limit(limit)
    ).all()
    return {"count": len(rows), "runs": [_serialize_run(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: SessionDep) -> dict:
    run = session.get(BrokerWatchRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    sources = session.exec(
        select(BrokerWatchSourceResult)
        .where(BrokerWatchSourceResult.run_id == run_id)
        .order_by(BrokerWatchSourceResult.id.asc())
    ).all()
    payload = _serialize_run(run)
    payload["sources"] = [_serialize_source_result(s) for s in sources]
    return payload


@router.get(
    "/health",
    summary="Broker watch health: latest run, 24h aggregate, per-source freshness, config",
)
def pipeline_health(session: SessionDep) -> dict:
    """Single-shot diagnostic for the broker watch job.

    ``stale_sources`` lists enabled brokers without a successful fetch in the
    last 7 days (plan criterion E); the daily reliability check reads it.
    """
    now = utc_now()
    since_24h = now - timedelta(hours=24)

    latest_run = session.exec(
        select(BrokerWatchRun).order_by(BrokerWatchRun.id.desc()).limit(1)
    ).first()

    runs_24h = list(
        session.exec(
            select(BrokerWatchRun)
            .where(BrokerWatchRun.started_at >= since_24h)
            .order_by(BrokerWatchRun.id.desc())
        ).all()
    )
    status_counts = Counter(r.status for r in runs_24h)
    last_24h = {
        "runs_total": len(runs_24h),
        "runs_success": status_counts.get("success", 0),
        "runs_partial": status_counts.get("partial_fail", 0),
        "runs_failed": status_counts.get("failed", 0),
        "documents_new": sum(r.documents_new for r in runs_24h),
        "events_new": sum(r.events_new for r in runs_24h),
        "errors_count": sum(r.errors_count for r in runs_24h),
        "first_run_at": runs_24h[-1].started_at if runs_24h else None,
        "last_run_at": runs_24h[0].started_at if runs_24h else None,
    }

    # Per-source last successful fetch (run start time of the newest success).
    last_success = dict(
        session.exec(
            select(BrokerWatchSourceResult.broker_slug, func.max(BrokerWatchRun.started_at))
            .join(BrokerWatchRun, BrokerWatchRun.id == BrokerWatchSourceResult.run_id)
            .where(BrokerWatchSourceResult.status == "success")
            .group_by(BrokerWatchSourceResult.broker_slug)
        ).all()
    )

    try:
        config = load_broker_config()
        brokers = config.brokers
        config_summary: dict = {
            "path": settings.BROKER_WATCH_CONFIG_PATH,
            "brokers_total": len(brokers),
            "brokers_enabled": [b.slug for b in config.enabled_brokers],
            "max_runs_per_day": config.defaults.max_runs_per_day,
            "user_agent": config.defaults.user_agent,
        }
    except Exception as exc:  # a broken config must not break /health
        brokers = []
        config_summary = {"path": settings.BROKER_WATCH_CONFIG_PATH, "error": str(exc)[:200]}

    sources = [
        {
            "slug": b.slug,
            "name": b.name,
            "adapter": b.adapter,
            "enabled": b.enabled,
            "last_success_at": last_success.get(b.slug),
        }
        for b in brokers
    ]
    stale_sources = [
        b.slug
        for b in brokers
        if b.enabled
        and (last_success.get(b.slug) is None or last_success[b.slug] < now - STALE_AFTER)
    ]

    if latest_run is None:
        verdict = "no_runs"
    elif latest_run.status == "running":
        verdict = "running"
    elif latest_run.status == "failed" or last_24h["runs_failed"] > 0:
        verdict = "degraded"
    elif stale_sources:
        verdict = "stale_sources"
    elif last_24h["runs_partial"] > 0:
        verdict = "partial"
    else:
        verdict = "healthy"

    return {
        "checked_at": now,
        "verdict": verdict,
        "latest_run": _serialize_run(latest_run) if latest_run else None,
        "last_24h": last_24h,
        "sources": sources,
        "stale_sources": stale_sources,
        "config": config_summary,
    }
