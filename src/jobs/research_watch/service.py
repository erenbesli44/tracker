"""One research watch run: listing -> new posts -> store -> digest -> checks.

Idempotent: a post is stored once per URL. A normal run looks only at posts dated within
``lookback_days`` and stores at most ``max_new_items``; a backfill (``since``) bypasses the
daily run limit and takes posts dated on or after ``since``, up to ``max_items``. A post whose
page cannot be fetched or parsed is stored as a ``fetch_failed`` stub and retried on later runs up
to MAX_FETCH_ATTEMPTS; digests that failed (model error or bad shape) are retried up to
MAX_DIGEST_ATTEMPTS. The run lock is per process: a Coolify run and an API-triggered run at the
same moment may race; the URL unique constraint keeps the data consistent.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from typing import Protocol

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from src.jobs.research_watch import parsers
from src.jobs.research_watch.config import ResearchSource, ResearchWatchConfig
from src.jobs.research_watch.digest import DIGEST_VERSION, check_digest, validate_digest
from src.jobs.research_watch.fetcher import HttpFetcher
from src.jobs.research_watch.models import ResearchItem, ResearchWatchRun
from src.models import utc_now

logger = logging.getLogger(__name__)

SKIPPED_RATE_LIMIT = "skipped_rate_limit"
MAX_DIGEST_ATTEMPTS = 3
MAX_FETCH_ATTEMPTS = 3
FETCH_FAILED = "fetch_failed"
DEFAULT_BACKFILL_MAX_ITEMS = 20
RETRY_STATUSES = ("pending", "failed")

DigestFn = Callable[..., dict]


class Fetcher(Protocol):  # HttpFetcher in production
    def get_text(self, url: str) -> str: ...


def _default_digest_fn(**kwargs) -> dict:
    from src.llm.service import generate_research_digest_json

    return generate_research_digest_json(**kwargs)


@dataclass
class RunSummary:
    run_id: int
    status: str = "running"
    items_seen: int = 0
    items_new: int = 0
    digests_ok: int = 0
    digests_flagged: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # informational, not errors


def run_once(
    engine: Engine,
    config: ResearchWatchConfig,
    fetcher: Fetcher | None = None,
    digest_fn: DigestFn | None = None,
    *,
    since: date | None = None,
    max_items: int | None = None,
) -> RunSummary:
    """Process every enabled source once; never raises for one source's or post's failure."""
    digest_fn = digest_fn or _default_digest_fn
    if fetcher is not None:
        return _run(engine, config, fetcher, digest_fn, since, max_items)
    with HttpFetcher(config.defaults.user_agent) as http_fetcher:
        return _run(engine, config, http_fetcher, digest_fn, since, max_items)


def _runs_started_today(session: Session) -> int:
    midnight = datetime.combine(utc_now().date(), time.min)
    return session.exec(
        select(func.count()).where(
            ResearchWatchRun.started_at >= midnight,
            ResearchWatchRun.status != SKIPPED_RATE_LIMIT,
        )
    ).one()


def _run(engine, config, fetcher, digest_fn, since, max_items) -> RunSummary:
    with Session(engine) as session:
        run = ResearchWatchRun()
        if since is None and _runs_started_today(session) >= config.defaults.max_runs_per_day:
            run.status = SKIPPED_RATE_LIMIT
            run.finished_at = utc_now()
        session.add(run)
        session.commit()
        session.refresh(run)
        if run.status == SKIPPED_RATE_LIMIT:
            logger.warning("Research watch run %d skipped: daily run limit reached", run.id)
            return RunSummary(run_id=run.id, status=SKIPPED_RATE_LIMIT)
        summary = RunSummary(run_id=run.id)

    if since is not None:
        summary.notes.append(f"backfill since {since.isoformat()}")
    for source in config.enabled_sources:
        try:
            _process_source(engine, config, source, fetcher, digest_fn, since, max_items, summary)
        except Exception as exc:
            logger.error("Source %s failed: %s", source.slug, exc, exc_info=True)
            summary.errors.append(f"{source.slug}: {type(exc).__name__}: {str(exc)[:300]}")

    if not summary.errors:
        summary.status = "success"
    elif summary.items_new or summary.digests_ok or summary.digests_flagged:
        summary.status = "partial_fail"
    else:
        summary.status = "failed"

    with Session(engine) as session:
        run = session.get(ResearchWatchRun, summary.run_id)
        run.finished_at = utc_now()
        run.status = summary.status
        run.items_seen = summary.items_seen
        run.items_new = summary.items_new
        run.digests_ok = summary.digests_ok
        run.digests_flagged = summary.digests_flagged
        run.errors_count = len(summary.errors)
        details = summary.notes + summary.errors
        run.error_details = json.dumps(details, ensure_ascii=False) if details else None
        session.add(run)
        session.commit()
    logger.info("Research watch run %d finished: %s", summary.run_id, summary)
    return summary


def _process_source(engine, config, source, fetcher, digest_fn, since, max_items, summary):
    if source.adapter != "tcmb_blog":
        raise ValueError(f"unknown adapter: {source.adapter}")
    entries = parsers.parse_tcmb_listing(fetcher.get_text(source.url), source.url)
    if not entries:
        raise ValueError("listing parsed to 0 posts (page layout changed?)")

    cutoff = since or (utc_now().date() - timedelta(days=config.defaults.lookback_days))
    limit = (max_items or DEFAULT_BACKFILL_MAX_ITEMS) if since else config.defaults.max_new_items
    candidates = [e for e in entries if e.published_at and e.published_at >= cutoff]
    summary.items_seen += len(candidates)

    with Session(engine) as session:
        known = {
            item.url: item
            for item in session.exec(
                select(ResearchItem).where(ResearchItem.source_slug == source.slug)
            )
        }
        new = sorted(
            (e for e in candidates if _wants_fetch(known.get(e.url))),
            key=lambda e: e.published_at,
        )
        if len(new) > limit:
            summary.notes.append(
                f"{source.slug}: {len(new)} posts to fetch, only the oldest {limit} this run"
            )
            new = new[:limit]
        for entry in new:
            item = known.get(entry.url) or ResearchItem(
                source_slug=source.slug, url=entry.url, title=entry.title or entry.url, sha256=""
            )
            try:
                _fill_item(item, entry, fetcher)
                summary.items_new += 1
            except Exception as exc:
                # Stored as a stub so a broken post is retried a few times, not on every run.
                logger.error("Post failed %s: %s", entry.url, exc, exc_info=True)
                item.review_status = FETCH_FAILED
                item.fetch_attempts += 1
                item.digest_error = f"fetch: {type(exc).__name__}: {str(exc)[:300]}"
                summary.errors.append(f"{entry.url}: {item.digest_error}")
            session.add(item)
            session.commit()

        todo = session.exec(
            select(ResearchItem)
            .where(
                ResearchItem.source_slug == source.slug,
                ResearchItem.review_status.in_(RETRY_STATUSES),
                ResearchItem.digest_attempts < MAX_DIGEST_ATTEMPTS,
            )
            .order_by(ResearchItem.published_at)
        ).all()
        for item in todo:
            digest_item(session, item, source, digest_fn)
            if item.review_status == "failed":
                summary.errors.append(f"{item.url}: digest: {item.digest_error}")
            elif item.checks_passed:
                summary.digests_ok += 1
            else:
                summary.digests_flagged += 1


def _wants_fetch(item: ResearchItem | None) -> bool:
    if item is None:
        return True
    return item.review_status == FETCH_FAILED and item.fetch_attempts < MAX_FETCH_ATTEMPTS


def _fill_item(item: ResearchItem, entry: parsers.ListingEntry, fetcher) -> None:
    article = parsers.parse_tcmb_article(fetcher.get_text(entry.url))
    if len(article.body_text) < 500:
        raise ValueError(f"article body too short ({len(article.body_text)} chars)")
    title = article.title or entry.title
    if not title:
        raise ValueError("article without title")
    item.title = title
    item.authors = json.dumps(article.authors or entry.authors, ensure_ascii=False)
    item.category = article.category or entry.category
    item.published_at = article.published_at or entry.published_at
    item.body_text = article.body_text
    item.sha256 = sha256(article.body_text.encode("utf-8")).hexdigest()
    item.fetched_at = utc_now()
    item.review_status = "pending"
    item.digest_error = None


def digest_item(session: Session, item: ResearchItem, source: ResearchSource, digest_fn) -> None:
    """Generate, validate and check one digest; stores the outcome on ``item`` and commits."""
    item.digest_attempts += 1
    try:
        raw = digest_fn(
            source_name=source.name,
            title=item.title,
            authors=", ".join(json.loads(item.authors)),
            published_at=item.published_at.isoformat() if item.published_at else "",
            category=item.category or "",
            body_text=item.body_text,
        )
        digest = validate_digest(raw)
    except Exception as exc:
        logger.warning("Digest failed for %s: %s", item.url, exc)
        item.review_status = "failed"
        item.digest_error = f"{type(exc).__name__}: {str(exc)[:400]}"
    else:
        problems = check_digest(digest, item.body_text)
        item.digest_json = json.dumps(digest, ensure_ascii=False)
        item.digest_version = DIGEST_VERSION
        item.digested_at = utc_now()
        item.checks_passed = not problems
        item.digest_error = "; ".join(problems) or None
        item.review_status = "auto" if (not problems and source.auto_publish) else "needs_review"
    session.add(item)
    session.commit()
    session.refresh(item)
