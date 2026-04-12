"""SQLModel tables for YouTube watch job run logs."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class YouTubeWatchRun(SQLModel, table=True):
    __tablename__ = "youtube_watch_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = Field(default=None)
    # running / success / partial_fail / failed
    status: str = Field(default="running", max_length=20)
    channels_scanned: int = Field(default=0)
    videos_detected: int = Field(default=0)
    videos_ingested: int = Field(default=0)
    videos_skipped: int = Field(default=0)
    errors_count: int = Field(default=0)
    error_details: Optional[str] = Field(default=None)  # JSON array of error strings


class YouTubeWatchChannelResult(SQLModel, table=True):
    __tablename__ = "youtube_watch_channel_result"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="youtube_watch_run.id", index=True)
    channel_identifier: str = Field(max_length=255)
    channel_name: str = Field(max_length=255)
    resolved_channel_id: Optional[str] = Field(default=None, max_length=100)
    videos_detected: int = Field(default=0)
    videos_ingested: int = Field(default=0)
    videos_skipped: int = Field(default=0)
    errors_count: int = Field(default=0)
    error_detail: Optional[str] = Field(default=None)
    # success / partial / failed
    status: str = Field(default="success", max_length=20)
