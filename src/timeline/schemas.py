from datetime import datetime
from typing import Optional

from src.models import CustomModel


class TopicInfo(CustomModel):
    id: int
    name: str
    slug: str
    parent_id: Optional[int]
    parent_name: Optional[str]


class TimelineEntry(CustomModel):
    mention_id: int
    video_id: int
    video_title: Optional[str]
    video_url: str
    published_at: Optional[datetime]
    summary: str
    sentiment: Optional[str]
    key_levels: Optional[list[str]]
    start_time: Optional[str]
    end_time: Optional[str]
    confidence: float


class TimelineResponse(CustomModel):
    topic: TopicInfo
    channel_id: Optional[int] = None
    person_id: Optional[int] = None
    total: int
    entries: list[TimelineEntry]


class PersonTopicSummary(CustomModel):
    topic: TopicInfo
    mention_count: int
    latest_sentiment: Optional[str]
    latest_summary: str
    latest_published_at: Optional[datetime]
    latest_video_url: Optional[str]


class PersonOverviewResponse(CustomModel):
    channel_id: Optional[int] = None
    person_id: Optional[int] = None
    topics: list[PersonTopicSummary]
