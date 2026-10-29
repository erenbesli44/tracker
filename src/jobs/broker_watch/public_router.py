"""Read-only API for broker model-portfolio events and compositions (OI-027 step 4).

Only events with ``review_status`` "auto" or "reviewed" are served (plan criterion D).
Compositions are served as tickers + entry dates only, without targets or weights
(plan §7 decision 2, criterion C). Document text is never served.
"""

import json
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func
from sqlmodel import Session, select

from src.database import SessionDep
from src.jobs.broker_watch.config import load_broker_config
from src.jobs.broker_watch.models import (
    Broker,
    BrokerDocument,
    ModelPortfolioEvent,
    ModelPortfolioSnapshot,
)
from src.models import utc_now

router = APIRouter(prefix="/brokers", tags=["brokers"])

SERVED_STATUSES = ("auto", "reviewed")
BROKER_EVENTS_LIMIT = 10


def _served():
    return ModelPortfolioEvent.review_status.in_(SERVED_STATUSES)


def _enabled_slugs() -> set[str]:
    try:
        return {b.slug for b in load_broker_config().enabled_brokers}
    except Exception:  # a broken config must not break the read API
        return set()


def _get_broker(session: Session, slug: str) -> Broker:
    broker = session.exec(select(Broker).where(Broker.slug == slug)).first()
    if broker is None:
        raise HTTPException(status_code=404, detail="broker_not_found")
    return broker


def _previous_lookup(
    session: Session, rows: list[tuple[ModelPortfolioEvent, Broker, BrokerDocument]]
) -> dict[tuple[int, str], list[ModelPortfolioEvent]]:
    """All served events of the (broker, ticker) pairs in ``rows``, oldest first."""
    if not rows:
        return {}
    broker_ids = {e.broker_id for e, _, _ in rows}
    tickers = {e.ticker for e, _, _ in rows}
    history = session.exec(
        select(ModelPortfolioEvent)
        .where(
            _served(),
            ModelPortfolioEvent.broker_id.in_(broker_ids),
            ModelPortfolioEvent.ticker.in_(tickers),
        )
        .order_by(ModelPortfolioEvent.event_date.asc(), ModelPortfolioEvent.id.asc())
    ).all()
    lookup: dict[tuple[int, str], list[ModelPortfolioEvent]] = {}
    for event in history:
        lookup.setdefault((event.broker_id, event.ticker), []).append(event)
    return lookup


def _previous(event: ModelPortfolioEvent, history: list[ModelPortfolioEvent]) -> dict | None:
    key = (event.event_date, event.id)
    earlier = [e for e in history if (e.event_date, e.id) < key]
    if not earlier:
        return None
    return {"action": earlier[-1].action, "event_date": earlier[-1].event_date}


def _serialize_event(
    event: ModelPortfolioEvent,
    broker: Broker,
    document: BrokerDocument,
    history: list[ModelPortfolioEvent],
) -> dict:
    return {
        "id": event.id,
        "broker": {"slug": broker.slug, "name": broker.name},
        "ticker": event.ticker,
        "action": event.action,
        "event_date": event.event_date,
        "reason_quote": event.reason_quote,
        "quote_page": event.quote_page,
        "recommendation": event.recommendation,
        "target_price": event.target_price,
        "document": {
            "url": document.file_url or document.source_url,
            "published_at": document.published_at,
        },
        "previous": _previous(event, history),
    }


def _list_events(
    session: Session,
    *,
    limit: int,
    broker_id: int | None = None,
    since: date | None = None,
) -> list[dict]:
    stmt = (
        select(ModelPortfolioEvent, Broker, BrokerDocument)
        .join(Broker, Broker.id == ModelPortfolioEvent.broker_id)
        .join(BrokerDocument, BrokerDocument.id == ModelPortfolioEvent.document_id)
        .where(_served())
    )
    if broker_id is not None:
        stmt = stmt.where(ModelPortfolioEvent.broker_id == broker_id)
    if since is not None:
        stmt = stmt.where(ModelPortfolioEvent.event_date >= since)
    stmt = stmt.order_by(
        ModelPortfolioEvent.event_date.desc(), ModelPortfolioEvent.id.desc()
    ).limit(limit)
    rows = list(session.exec(stmt).all())
    lookup = _previous_lookup(session, rows)
    return [_serialize_event(e, b, d, lookup.get((e.broker_id, e.ticker), [])) for e, b, d in rows]


def _serialize_composition(snapshot: ModelPortfolioSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    try:
        raw = json.loads(snapshot.items_json)
    except json.JSONDecodeError:
        raw = []
    items = [
        {"ticker": item.get("ticker"), "entry_date": item.get("entry_date")}
        for item in raw
        if isinstance(item, dict)
    ]
    return {"as_of": snapshot.as_of, "basis": snapshot.basis, "items": items}


@router.get("/", summary="Brokers with counts of served model-portfolio events")
def list_brokers(session: SessionDep) -> dict:
    stats = {
        broker_id: (count, last_date)
        for broker_id, count, last_date in session.exec(
            select(
                ModelPortfolioEvent.broker_id,
                func.count(ModelPortfolioEvent.id),
                func.max(ModelPortfolioEvent.event_date),
            )
            .where(_served())
            .group_by(ModelPortfolioEvent.broker_id)
        ).all()
    }
    enabled = _enabled_slugs()
    brokers = session.exec(select(Broker).order_by(Broker.id.asc())).all()
    return {
        "brokers": [
            {
                "slug": b.slug,
                "name": b.name,
                "site_url": b.site_url,
                "enabled": b.slug in enabled,
                "events_count": stats.get(b.id, (0, None))[0],
                "last_event_date": stats.get(b.id, (0, None))[1],
            }
            for b in brokers
        ]
    }


@router.get("/model-portfolio/events", summary="Served model-portfolio events, newest first")
def list_events(
    session: SessionDep,
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
    broker: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict:
    broker_id = _get_broker(session, broker).id if broker else None
    since = utc_now().date() - timedelta(days=days)
    events = _list_events(session, limit=limit, broker_id=broker_id, since=since)
    return {"count": len(events), "events": events}


@router.get("/{slug}/model-portfolio", summary="Latest composition and last served events")
def broker_model_portfolio(slug: str, session: SessionDep) -> dict:
    broker = _get_broker(session, slug)
    snapshot = session.exec(
        select(ModelPortfolioSnapshot)
        .where(ModelPortfolioSnapshot.broker_id == broker.id)
        .order_by(ModelPortfolioSnapshot.as_of.desc(), ModelPortfolioSnapshot.id.desc())
        .limit(1)
    ).first()
    return {
        "broker": {"slug": broker.slug, "name": broker.name, "site_url": broker.site_url},
        "composition": _serialize_composition(snapshot),
        "events": _list_events(session, limit=BROKER_EVENTS_LIMIT, broker_id=broker.id),
    }
