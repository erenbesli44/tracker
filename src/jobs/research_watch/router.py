"""Operator API for the research watch job: run history, the review queue, review decisions,
and a background run trigger (the Coolify terminal is unreliable for the tracker; a Coolify
scheduled task runs ``python -m src.jobs.research_watch.runner`` instead)."""

import json
import logging
import threading
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import Session, select

from src.database import SessionDep, engine
from src.jobs.research_watch import service
from src.jobs.research_watch.config import load_research_config
from src.jobs.research_watch.models import ResearchItem, ResearchWatchRun
from src.jobs.research_watch.serialize import item_payload
from src.models import utc_now

router = APIRouter(prefix="/jobs/research-watch", tags=["jobs"])
logger = logging.getLogger(__name__)

_lock = threading.Lock()
_state: dict = {"running": False}


def _background(fn, *args) -> None:
    try:
        fn(*args)
        _state.update(status="finished", error=None)
    except Exception as exc:  # a background task must not die silently
        logger.exception("Research watch background task failed")
        _state.update(status="error", error=str(exc)[:300])
    finally:
        _state.update(running=False, finished_at=utc_now())
        _lock.release()


def _start(background_tasks: BackgroundTasks, label: str, fn, *args) -> dict:
    if not _lock.acquire(blocking=False):
        return {**_state, "status": "already_running"}
    _state.clear()
    _state.update(running=True, task=label, started_at=utc_now())
    background_tasks.add_task(_background, fn, *args)
    return {"status": "accepted", "task": label}


def _run(since: date | None, max_items: int | None) -> None:
    summary = service.run_once(engine, load_research_config(), since=since, max_items=max_items)
    _state.update(run_id=summary.run_id, run_status=summary.status)


@router.post(
    "/run", status_code=status.HTTP_202_ACCEPTED, summary="Start one run in the background"
)
def start_run(
    background_tasks: BackgroundTasks,
    since: Annotated[date | None, Query(description="Backfill: posts dated on/after")] = None,
    max_items: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> dict:
    if since is not None and (since > utc_now().date() or since.year < 2000):
        raise HTTPException(status_code=422, detail="since must be a past date from 2000 on")
    if max_items is not None and since is None:
        raise HTTPException(status_code=422, detail="max_items applies to a backfill only")
    return _start(background_tasks, "run", _run, since, max_items)


@router.get("/run", summary="State of the last background task")
def run_state() -> dict:
    return _state


@router.get("/runs")
def list_runs(session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 20) -> dict:
    rows = session.exec(
        select(ResearchWatchRun).order_by(ResearchWatchRun.id.desc()).limit(limit)
    ).all()
    runs = [
        {**r.model_dump(), "error_details": json.loads(r.error_details or "[]")} for r in rows
    ]
    return {"count": len(runs), "runs": runs}


@router.get("/items", summary="Review queue: every stored post with its digest and checks")
def list_items(
    session: SessionDep,
    review_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    stmt = select(ResearchItem)
    if review_status:
        stmt = stmt.where(ResearchItem.review_status == review_status)
    rows = session.exec(stmt.order_by(ResearchItem.published_at.desc()).limit(limit)).all()
    config = _safe_config()
    items = [
        {
            **item_payload(r, config),
            "review_status": r.review_status,
            "checks_passed": r.checks_passed,
            "problems": r.digest_error,
            "digest_attempts": r.digest_attempts,
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


class ReviewDecision(BaseModel):
    review_status: Literal["reviewed", "rejected", "needs_review"]


@router.patch("/items/{item_id}", summary="Record the owner's review decision")
def review_item(item_id: int, decision: ReviewDecision, session: SessionDep) -> dict:
    item = session.get(ResearchItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="item_not_found")
    if decision.review_status == "reviewed" and not item.digest_json:
        raise HTTPException(status_code=409, detail="no digest to approve")
    item.review_status = decision.review_status
    session.add(item)
    session.commit()
    return {"id": item.id, "review_status": item.review_status}


def _redigest(item_id: int) -> None:
    config = load_research_config()
    with Session(engine) as session:
        item = session.get(ResearchItem, item_id)
        source = config.source(item.source_slug)
        if source is None:
            raise ValueError(f"source {item.source_slug} not in config")
        service.digest_item(session, item, source, service._default_digest_fn)
        _state.update(item_id=item_id, review_status=item.review_status)


@router.post(
    "/items/{item_id}/redigest",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Generate the digest again (e.g. after a prompt change); it returns to review",
)
def redigest_item(item_id: int, background_tasks: BackgroundTasks, session: SessionDep) -> dict:
    if session.get(ResearchItem, item_id) is None:
        raise HTTPException(status_code=404, detail="item_not_found")
    return _start(background_tasks, f"redigest {item_id}", _redigest, item_id)


def _safe_config():
    try:
        return load_research_config()
    except Exception:
        return None
