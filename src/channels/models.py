from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class YouTubeChannel(SQLModel, table=True):
    __tablename__ = "youtube_channel"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255, unique=True, index=True)
    platform: str = Field(default="youtube", max_length=50)
    channel_handle: Optional[str] = Field(default=None, max_length=255, index=True)
    youtube_channel_id: Optional[str] = Field(default=None, max_length=100, index=True)
    channel_url: Optional[str] = Field(default=None, max_length=500)
    bio: Optional[str] = Field(default=None)
    # Channel-level topic focus: slug of the main topic (e.g. "ekonomi")
    primary_topic_slug: Optional[str] = Field(default=None, max_length=100)
    # JSON array of expected subtopic slugs (e.g. '["bist","altin","dolar"]')
    expected_subtopics: Optional[str] = Field(default=None)
    legacy_person_id: Optional[int] = Field(
        default=None,
        foreign_key="person.id",
        unique=True,
        index=True,
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = Field(default=None)
