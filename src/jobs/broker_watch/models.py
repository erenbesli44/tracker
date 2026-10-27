"""SQLModel tables for broker model-portfolio tracking and the broker watch job logs.

Data model from docs/research/2026-10-26-model-portfolio-pilot-plan.md (doviz repo).
``broker_document.text`` is kept for audit only and is never served.
"""

from datetime import date, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from src.models import utc_now


class Broker(SQLModel, table=True):
    __tablename__ = "broker"

    id: int | None = Field(default=None, primary_key=True)
    slug: str = Field(max_length=100, unique=True, index=True)
    name: str = Field(max_length=255)
    site_url: str = Field(max_length=500)
    # isy_rss / yk_json / gedik_page
    adapter: str = Field(max_length=40)
    enabled: bool = Field(default=True)
    notice_text: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)


class BrokerDocument(SQLModel, table=True):
    __tablename__ = "broker_document"

    id: int | None = Field(default=None, primary_key=True)
    broker_id: int = Field(foreign_key="broker.id", index=True)
    # change_note / composition / monthly
    kind: str = Field(max_length=20)
    source_url: str = Field(max_length=1000)
    file_url: str | None = Field(default=None, max_length=1000)
    sha256: str = Field(max_length=64, unique=True, index=True)
    published_at: date | None = Field(default=None)
    fetched_at: datetime = Field(default_factory=utc_now)
    text: str = Field(default="")
    # parsed / needs_review / failed
    parse_status: str = Field(default="parsed", max_length=20)
    parse_error: str | None = Field(default=None)


class ModelPortfolioEvent(SQLModel, table=True):
    __tablename__ = "model_portfolio_event"
    __table_args__ = (
        UniqueConstraint(
            "broker_id", "ticker", "action", "event_date", name="uq_model_portfolio_event"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    broker_id: int = Field(foreign_key="broker.id", index=True)
    document_id: int = Field(foreign_key="broker_document.id", index=True)
    ticker: str = Field(max_length=10)
    # added / removed / weight_up / weight_down
    action: str = Field(max_length=20)
    event_date: date = Field(index=True)
    # Verbatim substring of broker_document.text, at most 300 characters.
    reason_quote: str = Field(max_length=300)
    quote_page: int | None = Field(default=None)
    target_price: float | None = Field(default=None)
    recommendation: str | None = Field(default=None, max_length=20)
    # auto / needs_review / reviewed / rejected
    review_status: str = Field(default="auto", max_length=20)
    created_at: datetime = Field(default_factory=utc_now)


class ModelPortfolioSnapshot(SQLModel, table=True):
    __tablename__ = "model_portfolio_snapshot"

    id: int | None = Field(default=None, primary_key=True)
    broker_id: int = Field(foreign_key="broker.id", index=True)
    document_id: int = Field(foreign_key="broker_document.id", index=True)
    as_of: date = Field(index=True)
    # current: holdings as of as_of / pre_change: holdings before the changes announced
    # in the same document (Yapı Kredi's page-2 table). Read by the consistency check.
    basis: str = Field(default="current", max_length=12)
    # JSON array of {"ticker": str, "entry_date": "YYYY-MM-DD" | null}
    items_json: str = Field(default="[]")
    created_at: datetime = Field(default_factory=utc_now)


class BrokerWatchRun(SQLModel, table=True):
    __tablename__ = "broker_watch_run"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = Field(default=None)
    # running / success / partial_fail / failed / skipped_rate_limit
    status: str = Field(default="running", max_length=20)
    sources_scanned: int = Field(default=0)
    documents_new: int = Field(default=0)
    events_new: int = Field(default=0)
    snapshots_new: int = Field(default=0)
    errors_count: int = Field(default=0)
    error_details: str | None = Field(default=None)  # JSON array of error strings


class BrokerWatchSourceResult(SQLModel, table=True):
    __tablename__ = "broker_watch_source_result"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="broker_watch_run.id", index=True)
    broker_slug: str = Field(max_length=100, index=True)
    broker_name: str = Field(max_length=255)
    # success / partial / failed
    status: str = Field(default="success", max_length=20)
    documents_seen: int = Field(default=0)
    documents_new: int = Field(default=0)
    events_new: int = Field(default=0)
    snapshots_new: int = Field(default=0)
    errors_count: int = Field(default=0)
    error_detail: str | None = Field(default=None)
    documents_skipped_robots: int = Field(default=0)  # robots.txt skips (also in errors_count)
    events_flagged: int = Field(default=0)  # by the snapshot-consistency check
