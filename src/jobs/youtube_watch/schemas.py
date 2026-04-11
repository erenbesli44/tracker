"""Typed schemas for YouTube watch job orchestration."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProviderVideoItem:
    channel_id: str
    video_id: str
    video_url: str
    title: str | None
    published_at: datetime | None


@dataclass
class ChannelJobResult:
    channel_pk: int | None = None
    person_id: int | None = None
    channel_id: str
    videos_detected: int = 0
    videos_ingested: int = 0
    videos_skipped: int = 0
    errors_count: int = 0


@dataclass
class JobRunSummary:
    channels_scanned: int = 0
    videos_detected: int = 0
    videos_ingested: int = 0
    videos_skipped: int = 0
    errors_count: int = 0
