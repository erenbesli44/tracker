"""SQLModel tables for the research watch job (OI-041).

``research_item.body_text`` is kept for the numbers check and audit; it is never served.
``digest_json`` holds the validated digest (schema in ``digest.py``).
"""

from datetime import date, datetime

from sqlmodel import Field, SQLModel

from src.models import utc_now


class ResearchItem(SQLModel, table=True):
    __tablename__ = "research_item"

    id: int | None = Field(default=None, primary_key=True)
    source_slug: str = Field(max_length=100, index=True)
    url: str = Field(max_length=1000, unique=True, index=True)
    title: str = Field(max_length=500)
    authors: str = Field(default="[]")  # JSON list of names
    category: str | None = Field(default=None, max_length=120)
    published_at: date | None = Field(default=None, index=True)
    body_text: str = Field(default="")
    sha256: str = Field(max_length=64, index=True)
    fetched_at: datetime = Field(default_factory=utc_now)
    digest_json: str | None = Field(default=None)
    digest_version: str | None = Field(default=None, max_length=40)
    digest_attempts: int = Field(default=0)
    digest_error: str | None = Field(default=None)
    digested_at: datetime | None = Field(default=None)
    # Every number in the digest occurs in body_text, and the quote is verbatim.
    checks_passed: bool | None = Field(default=None)
    # pending (no digest yet) / failed (LLM error, retried) / needs_review / auto / reviewed /
    # rejected. Only auto and reviewed are served.
    review_status: str = Field(default="pending", max_length=20, index=True)


class ResearchWatchRun(SQLModel, table=True):
    __tablename__ = "research_watch_run"

    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utc_now, index=True)
    finished_at: datetime | None = Field(default=None)
    # running / success / partial_fail / failed / skipped_rate_limit
    status: str = Field(default="running", max_length=30)
    items_seen: int = Field(default=0)
    items_new: int = Field(default=0)
    digests_ok: int = Field(default=0)
    digests_flagged: int = Field(default=0)
    errors_count: int = Field(default=0)
    error_details: str | None = Field(default=None)  # JSON list of strings
