"""Orchestration for one broker watch run: discover -> fetch -> parse -> store -> check.

Idempotent: a document is stored once per sha256 (PDF URLs are also skipped when
already stored), events once per (broker, ticker, action, event_date). At most
``max_runs_per_day`` runs are started per UTC day; later ones are recorded as
``skipped_rate_limit`` and do nothing.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from src.jobs.broker_watch import parsers
from src.jobs.broker_watch.adapters import (
    COMPOSITION,
    DiscoveredDocument,
    FetchedDocument,
    HttpFetcher,
    RobotsDisallowed,
    SourceFetcher,
)
from src.jobs.broker_watch.config import BrokerWatchConfig, WatchedBroker
from src.jobs.broker_watch.models import (
    Broker,
    BrokerDocument,
    BrokerWatchRun,
    BrokerWatchSourceResult,
    ModelPortfolioEvent,
    ModelPortfolioSnapshot,
)
from src.models import utc_now

logger = logging.getLogger(__name__)

CHANGE_NOTE_PARSERS = {
    "isy_rss": parsers.parse_isy_rss,
    "yk_json": parsers.parse_yk_json,
    "gedik_page": parsers.parse_gedik_page,
}
SKIPPED_RATE_LIMIT = "skipped_rate_limit"


@dataclass
class SourceJobResult:
    broker_slug: str
    broker_name: str
    status: str = "success"  # success / partial / failed
    documents_seen: int = 0
    documents_new: int = 0
    events_new: int = 0
    snapshots_new: int = 0
    errors_count: int = 0
    error_detail: str | None = None
    documents_skipped_robots: int = 0  # also counted in errors_count / error_detail
    events_flagged: int = 0  # by the snapshot-consistency check


@dataclass
class JobRunSummary:
    run_id: int
    status: str = "running"
    sources_scanned: int = 0
    sources_failed: int = 0
    documents_new: int = 0
    events_new: int = 0
    snapshots_new: int = 0
    errors_count: int = 0
    sources: list[SourceJobResult] = field(default_factory=list)


def run_once(
    engine: Engine, config: BrokerWatchConfig, fetcher: SourceFetcher | None = None
) -> JobRunSummary:
    """Process every enabled broker once; never raises for a single source's failure."""
    if fetcher is not None:
        return _run_once(engine, config, fetcher)
    with HttpFetcher(config.defaults.user_agent) as http_fetcher:
        return _run_once(engine, config, http_fetcher)


def _runs_started_today(session: Session) -> int:
    midnight = datetime.combine(utc_now().date(), time.min)
    stmt = select(func.count()).where(
        BrokerWatchRun.started_at >= midnight, BrokerWatchRun.status != SKIPPED_RATE_LIMIT
    )
    return session.exec(stmt).one()


def _run_once(engine: Engine, config: BrokerWatchConfig, fetcher: SourceFetcher) -> JobRunSummary:
    with Session(engine) as session:
        run = BrokerWatchRun()
        if _runs_started_today(session) >= config.defaults.max_runs_per_day:
            run.status = SKIPPED_RATE_LIMIT
            run.finished_at = utc_now()
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        if run.status == SKIPPED_RATE_LIMIT:
            logger.warning(
                "Broker watch run %d skipped: max_runs_per_day=%d reached",
                run_id,
                config.defaults.max_runs_per_day,
            )
            return JobRunSummary(run_id=run_id, status=SKIPPED_RATE_LIMIT)

    summary = JobRunSummary(run_id=run_id)
    errors: list[str] = []
    logger.info("Broker watch run %d started (brokers=%d)", run_id, len(config.enabled_brokers))

    for broker_cfg in config.enabled_brokers:
        result = _process_source(engine, broker_cfg, fetcher)
        summary.sources.append(result)
        summary.sources_scanned += 1
        summary.sources_failed += result.status == "failed"
        summary.documents_new += result.documents_new
        summary.events_new += result.events_new
        summary.snapshots_new += result.snapshots_new
        summary.errors_count += result.errors_count
        if result.error_detail:
            errors.append(f"{broker_cfg.name}: {result.error_detail}")

        with Session(engine) as session:
            session.add(
                BrokerWatchSourceResult(
                    run_id=run_id,
                    broker_slug=result.broker_slug,
                    broker_name=result.broker_name,
                    status=result.status,
                    documents_seen=result.documents_seen,
                    documents_new=result.documents_new,
                    events_new=result.events_new,
                    snapshots_new=result.snapshots_new,
                    errors_count=result.errors_count,
                    error_detail=result.error_detail,
                )
            )
            session.commit()
        logger.info(
            "Source %s: status=%s seen=%d new_docs=%d new_events=%d flagged=%d "
            "robots_skipped=%d errors=%d",
            broker_cfg.slug,
            result.status,
            result.documents_seen,
            result.documents_new,
            result.events_new,
            result.events_flagged,
            result.documents_skipped_robots,
            result.errors_count,
        )

    if summary.errors_count == 0:
        summary.status = "success"
    elif summary.sources_failed == summary.sources_scanned:
        summary.status = "failed"
    else:
        summary.status = "partial_fail"

    with Session(engine) as session:
        run = session.get(BrokerWatchRun, run_id)
        run.finished_at = utc_now()
        run.status = summary.status
        run.sources_scanned = summary.sources_scanned
        run.documents_new = summary.documents_new
        run.events_new = summary.events_new
        run.snapshots_new = summary.snapshots_new
        run.errors_count = summary.errors_count
        run.error_details = json.dumps(errors) if errors else None
        session.add(run)
        session.commit()

    logger.info("Broker watch run %d finished: %s", run_id, summary)
    return summary


def _process_source(
    engine: Engine, broker_cfg: WatchedBroker, fetcher: SourceFetcher
) -> SourceJobResult:
    result = SourceJobResult(broker_slug=broker_cfg.slug, broker_name=broker_cfg.name)
    with Session(engine) as session:
        broker = _upsert_broker(session, broker_cfg)
        try:
            docs = fetcher.discover(broker_cfg)
        except Exception as exc:
            logger.error("Discovery failed for %s: %s", broker_cfg.slug, exc, exc_info=True)
            result.status = "failed"
            result.errors_count = 1
            result.error_detail = f"discover: {type(exc).__name__}: {str(exc)[:400]}"
            return result

        for doc in docs:
            result.documents_seen += 1
            try:
                # A PDF already stored under its URL is not downloaded again; the HTML
                # composition page is re-read every run and deduplicated by content hash.
                is_known_pdf = doc.file_url and doc.kind != COMPOSITION
                if is_known_pdf and _has_file_url(session, doc.file_url):
                    continue
                fetched = fetcher.fetch(broker_cfg, doc)
                if _has_sha256(session, fetched.sha256):
                    continue
                events_new, snapshots_new, failure = _store_document(
                    session, broker, broker_cfg, doc, fetched
                )
                result.documents_new += 1
                result.events_new += events_new
                result.snapshots_new += snapshots_new
                if failure:
                    result.errors_count += 1
                    result.error_detail = f"{doc.file_url or doc.source_url}: {failure}"
            except RobotsDisallowed as exc:
                logger.warning("robots.txt disallows %s (%s)", exc, broker_cfg.slug)
                result.documents_skipped_robots += 1
                result.errors_count += 1
                result.error_detail = f"robots.txt disallows {exc}"
            except Exception as exc:
                session.rollback()
                logger.error("Document failed for %s: %s", broker_cfg.slug, exc, exc_info=True)
                result.errors_count += 1
                result.error_detail = (
                    f"{doc.file_url or doc.source_url}: {type(exc).__name__}: {str(exc)[:300]}"
                )

        try:
            result.events_flagged = check_snapshot_consistency(session, broker.id)
        except Exception as exc:  # must not leave the run hanging in "running"
            session.rollback()
            logger.error("Consistency check failed for %s: %s", broker_cfg.slug, exc, exc_info=True)
            result.errors_count += 1
            result.error_detail = f"consistency check: {type(exc).__name__}: {str(exc)[:300]}"

    if result.errors_count and result.errors_count >= result.documents_seen:
        result.status = "failed"
    elif result.errors_count:
        result.status = "partial"
    return result


def _upsert_broker(session: Session, cfg: WatchedBroker) -> Broker:
    broker = session.exec(select(Broker).where(Broker.slug == cfg.slug)).first()
    if broker is None:
        broker = Broker(slug=cfg.slug, name=cfg.name, site_url=cfg.site_url, adapter=cfg.adapter)
    broker.name = cfg.name
    broker.site_url = cfg.site_url
    broker.adapter = cfg.adapter
    broker.enabled = cfg.enabled
    broker.notice_text = cfg.notice_text
    session.add(broker)
    session.commit()
    session.refresh(broker)
    return broker


def _has_file_url(session: Session, file_url: str) -> bool:
    stmt = select(BrokerDocument.id).where(BrokerDocument.file_url == file_url)
    return session.exec(stmt).first() is not None


def _has_sha256(session: Session, sha256: str) -> bool:
    stmt = select(BrokerDocument.id).where(BrokerDocument.sha256 == sha256)
    return session.exec(stmt).first() is not None


def _has_event(
    session: Session, broker_id: int, ticker: str, action: str, event_date: date
) -> bool:
    stmt = select(ModelPortfolioEvent.id).where(
        ModelPortfolioEvent.broker_id == broker_id,
        ModelPortfolioEvent.ticker == ticker,
        ModelPortfolioEvent.action == action,
        ModelPortfolioEvent.event_date == event_date,
    )
    return session.exec(stmt).first() is not None


def _parse(
    adapter: str, kind: str, text: str, published_at: date | None
) -> parsers.ParsedDocument:
    if kind == COMPOSITION:
        holdings = parsers.parse_gedik_composition(text)
        return parsers.ParsedDocument(
            text=parsers.normalize_text(text),
            composition=holdings,
            warnings=[] if holdings else ["composition table not found"],
        )
    return CHANGE_NOTE_PARSERS[adapter](text, published_at=published_at)


def _store_document(
    session: Session,
    broker: Broker,
    cfg: WatchedBroker,
    doc: DiscoveredDocument,
    fetched: FetchedDocument,
) -> tuple[int, int, str | None]:
    """Insert the document, its new events and snapshot.

    Returns (events_new, snapshots_new, failure); ``failure`` is the parse_error of a
    document stored with parse_status "failed", which the caller counts as an error.
    """
    try:
        parsed = _parse(cfg.adapter, doc.kind, fetched.text, doc.published_at)
        flagged = parsed.needs_review or any(e.needs_review for e in parsed.events)
        if doc.kind == COMPOSITION:
            status = "parsed" if parsed.composition else "failed"
        elif flagged or not parsed.events:
            status = "needs_review"
        else:
            status = "parsed"
    except Exception as exc:  # a parser bug must not lose the document
        source = doc.file_url or doc.source_url
        logger.error("Parse failed for %s: %s", source, exc, exc_info=True)
        parsed = parsers.ParsedDocument(text=parsers.normalize_text(fetched.text))
        parsed.warnings.append(f"{type(exc).__name__}: {str(exc)[:400]}")
        status = "failed"
    # Warnings are kept for audit whatever the status (e.g. a skipped TCMB token).
    error = "; ".join(parsed.warnings)[:1000] or None
    if parsed.warnings:
        logger.info("Parse warnings for %s: %s", doc.file_url or doc.source_url, parsed.warnings)

    document = BrokerDocument(
        broker_id=broker.id,
        kind=doc.kind,
        source_url=doc.source_url,
        file_url=doc.file_url,
        sha256=fetched.sha256,
        published_at=doc.published_at,
        text=parsed.text,
        parse_status=status,
        parse_error=error,
    )
    session.add(document)
    session.flush()

    today = utc_now().date()
    events_new = 0
    for event in parsed.events:
        event_date = event.event_date or doc.published_at or today
        if _has_event(session, broker.id, event.ticker, event.action, event_date):
            continue
        session.add(
            ModelPortfolioEvent(
                broker_id=broker.id,
                document_id=document.id,
                ticker=event.ticker,
                action=event.action,
                event_date=event_date,
                reason_quote=event.reason_quote[: parsers.MAX_QUOTE_CHARS],
                quote_page=event.quote_page,
                target_price=event.target_price,
                recommendation=event.recommendation,
                review_status="needs_review" if event.needs_review else "auto",
            )
        )
        events_new += 1

    snapshots_new = 0
    if parsed.composition:
        items = [
            {"ticker": h.ticker, "entry_date": h.entry_date.isoformat() if h.entry_date else None}
            for h in parsed.composition
        ]
        session.add(
            ModelPortfolioSnapshot(
                broker_id=broker.id,
                document_id=document.id,
                as_of=doc.published_at or today,
                basis=parsed.composition_basis,
                items_json=json.dumps(items),
            )
        )
        snapshots_new = 1

    session.commit()
    return events_new, snapshots_new, error if status == "failed" else None


def check_snapshot_consistency(session: Session, broker_id: int) -> int:
    """Plan criterion D: an ``auto`` added/removed event must agree with the broker's
    first snapshot dated on or after the event; otherwise it becomes ``needs_review``.

    A ``current`` snapshot lists an added ticker and not a removed one. A ``pre_change``
    snapshot of the event's own date (Yapı Kredi's page-2 table) is the opposite: the
    removed ticker is still listed and the added one not yet. Weight events and events
    without a later snapshot are left alone. Returns the number of events flagged.
    """
    events = session.exec(
        select(ModelPortfolioEvent).where(
            ModelPortfolioEvent.broker_id == broker_id,
            ModelPortfolioEvent.review_status == "auto",
            ModelPortfolioEvent.action.in_((parsers.ADDED, parsers.REMOVED)),
        )
    ).all()
    flagged = 0
    for event in events:
        snapshot = session.exec(
            select(ModelPortfolioSnapshot)
            .where(
                ModelPortfolioSnapshot.broker_id == broker_id,
                ModelPortfolioSnapshot.as_of >= event.event_date,
            )
            .order_by(ModelPortfolioSnapshot.as_of, ModelPortfolioSnapshot.id)
        ).first()
        if snapshot is None:
            continue
        listed = event.ticker in {item["ticker"] for item in json.loads(snapshot.items_json)}
        expected = event.action == parsers.ADDED
        if snapshot.basis == "pre_change" and snapshot.as_of == event.event_date:
            expected = not expected
        if listed == expected:
            continue
        note = (
            f"consistency: {event.ticker} {event.action} {event.event_date} but the "
            f"{snapshot.basis} snapshot of {snapshot.as_of} "
            f"{'lists' if listed else 'does not list'} it"
        )
        logger.warning("Event %d flagged for review: %s", event.id, note)
        event.review_status = "needs_review"
        document = session.get(BrokerDocument, event.document_id)
        document.parse_error = "; ".join(filter(None, [document.parse_error, note]))[:1000]
        if document.parse_status == "parsed":
            document.parse_status = "needs_review"
        session.add(event)
        session.add(document)
        flagged += 1
    session.commit()
    return flagged
