from datetime import datetime
from typing import Optional

from pydantic import Field

from src.models import CustomModel


class TopicCreate(CustomModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: Optional[int] = None
    description: Optional[str] = None


class TopicResponse(CustomModel):
    id: int
    name: str
    slug: str
    parent_id: Optional[int]
    description: Optional[str]
    created_at: datetime


class TopicNode(CustomModel):
    """Topic with nested sub-topics for tree responses."""

    id: int
    name: str
    slug: str
    description: Optional[str]
    sub_topics: list["TopicNode"] = []


TopicNode.model_rebuild()


class ChannelOpinionEntry(CustomModel):
    mention_id: int
    video_id: int
    video_title: Optional[str] = None
    video_url: str
    published_at: Optional[datetime] = None
    summary: str
    sentiment: Optional[str] = None
    key_levels: Optional[list[str]] = None
    confidence: float


class ChannelOpinionGroup(CustomModel):
    channel_id: int
    channel_name: str
    channel_slug: str
    channel_handle: Optional[str] = None
    mention_count: int
    latest_sentiment: Optional[str] = None
    entries: list[ChannelOpinionEntry]


class TopicOpinionsResponse(CustomModel):
    topic: TopicResponse
    total_channels: int
    channel_opinions: list[ChannelOpinionGroup]
