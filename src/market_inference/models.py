from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class MarketInferenceRun(SQLModel, table=True):
    __tablename__ = "market_inference_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_date: str = Field(max_length=10, unique=True, index=True)  # "YYYY-MM-DD"
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = Field(default=None)
    status: str = Field(default="running", max_length=40)
    # "running" | "success" | "skipped_no_new_videos" | "partial_fail" | "failed"
    window_start: Optional[datetime] = Field(default=None)
    window_end: Optional[datetime] = Field(default=None)
    model: Optional[str] = Field(default=None, max_length=100)
    prompt_version: str = Field(default="v1", max_length=20)
    error: Optional[str] = Field(default=None)


class MarketInferenceTopic(SQLModel, table=True):
    __tablename__ = "market_inference_topic"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="market_inference_run.id", index=True)
    topic_key: str = Field(max_length=40, index=True)
    # e.g. bitcoin | dolar-tl | bist | us-markets | altin | gumus | ...
    direction: str = Field(max_length=20)
    # "up" | "down" | "sideways" | "mixed"
    confidence: float = Field(default=0.0)  # 0.0–1.0
    summary: str  # 2-3 Turkish sentences, complete
    tags: Optional[str] = Field(default=None)  # JSON array of tag strings
    status: str = Field(default="new", max_length=20)
    # "new" | "updated" | "carried_over" | "error"
    changed_from_prev: bool = Field(default=False)
    change_reason: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)


class MarketInferenceSource(SQLModel, table=True):
    __tablename__ = "market_inference_source"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="market_inference_run.id", index=True)
    topic_key: str = Field(max_length=40)
    video_id: int = Field(foreign_key="video.id", index=True)
    weight_used: float = Field(default=1.0)
    contribution_note: Optional[str] = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=utc_now)
