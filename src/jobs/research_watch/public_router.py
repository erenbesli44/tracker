"""Read-only API for research digests (OI-041). Serves only ``auto`` and ``reviewed`` digests;
the post's body text is never served (link to the source instead).

``days=N`` means ``published_at >= today_utc - N``.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from src.database import SessionDep
from src.jobs.research_watch.config import load_research_config
from src.jobs.research_watch.digest import ALLOWED_TOPICS
from src.jobs.research_watch.models import ResearchItem
from src.jobs.research_watch.serialize import item_payload
from src.models import utc_now

router = APIRouter(prefix="/research", tags=["research"])

SERVED_STATUSES = ("auto", "reviewed")


def _config():
    try:
        return load_research_config()
    except Exception:  # a broken config must not break the read API
        return None


@router.get("/digests", summary="Served research digests, newest first")
def list_digests(
    session: SessionDep,
    source: Annotated[str | None, Query(pattern=r"^[a-z0-9-]+$")] = None,
    topic: Annotated[str | None, Query()] = None,
    days: Annotated[int, Query(ge=1, le=3650)] = 365,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    if topic is not None and topic not in ALLOWED_TOPICS:
        raise HTTPException(status_code=422, detail="unknown topic")
    stmt = select(ResearchItem).where(
        ResearchItem.review_status.in_(SERVED_STATUSES),
        ResearchItem.published_at >= utc_now().date() - timedelta(days=days),
    )
    if source:
        stmt = stmt.where(ResearchItem.source_slug == source)
    rows = session.exec(stmt.order_by(ResearchItem.published_at.desc())).all()
    config = _config()
    items = [item_payload(r, config) for r in rows]
    if topic:
        items = [i for i in items if topic in (i["digest"] or {}).get("topics", [])]
    items = items[:limit]
    return {"count": len(items), "items": items}


@router.get("/digests/{item_id}", summary="One served research digest")
def get_digest(item_id: int, session: SessionDep) -> dict:
    item = session.get(ResearchItem, item_id)
    if item is None or item.review_status not in SERVED_STATUSES:
        raise HTTPException(status_code=404, detail="digest_not_found")
    return item_payload(item, _config())
