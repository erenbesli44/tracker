"""Read-only HTTP access to Twitter posting job history."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from src.database import get_session
from src.jobs.twitter_post.models import TwitterPost, TwitterPostRun

router = APIRouter(prefix="/jobs/twitter-post", tags=["jobs"])


def _serialize_run(run: TwitterPostRun) -> dict:
    error_details: list[str] | None = None
    if run.error_details:
        try:
            parsed = json.loads(run.error_details)
            error_details = (
                [str(x) for x in parsed]
                if isinstance(parsed, list)
                else [str(parsed)]
            )
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
        "candidates_found": run.candidates_found,
        "posted": run.posted,
        "skipped": run.skipped,
        "failed": run.failed,
        "error_details": error_details,
    }


def _serialize_post(post: TwitterPost) -> dict:
    return {
        "id": post.id,
        "video_id": post.video_id,
        "run_id": post.run_id,
        "status": post.status,
        "tweet_id": post.tweet_id,
        "tweet_url": post.tweet_url,
        "tweet_text": post.tweet_text,
        "error_message": post.error_message,
        "attempt_count": post.attempt_count,
        "created_at": post.created_at,
        "posted_at": post.posted_at,
    }


@router.get("/runs")
def list_runs(
    limit: int = Query(20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    rows = session.exec(
        select(TwitterPostRun).order_by(TwitterPostRun.id.desc()).limit(limit)
    ).all()
    return {"count": len(rows), "runs": [_serialize_run(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)) -> dict:
    run = session.get(TwitterPostRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    posts = session.exec(
        select(TwitterPost)
        .where(TwitterPost.run_id == run_id)
        .order_by(TwitterPost.id.asc())
    ).all()
    payload = _serialize_run(run)
    payload["posts"] = [_serialize_post(p) for p in posts]
    return payload


@router.get("/posts")
def list_posts(
    limit: int = Query(50, ge=1, le=500),
    status: str | None = Query(None, description="Filter by posted/failed/skipped"),
    session: Session = Depends(get_session),
) -> dict:
    statement = select(TwitterPost).order_by(TwitterPost.id.desc()).limit(limit)
    if status:
        statement = (
            select(TwitterPost)
            .where(TwitterPost.status == status)
            .order_by(TwitterPost.id.desc())
            .limit(limit)
        )
    rows = session.exec(statement).all()
    return {"count": len(rows), "posts": [_serialize_post(p) for p in rows]}
