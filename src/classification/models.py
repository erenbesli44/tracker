from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from src.models import utc_now


class TopicMention(SQLModel, table=True):
    __tablename__ = "topic_mention"
    __table_args__ = (
        Index("ix_topic_mention_person_topic_created", "person_id", "topic_id", "created_at"),
        Index("ix_topic_mention_channel_topic_created", "channel_id", "topic_id", "created_at"),
        Index("ix_topic_mention_video_topic", "video_id", "topic_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", index=True)
    channel_id: Optional[int] = Field(
        default=None,
        foreign_key="youtube_channel.id",
        index=True,
    )
    person_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    topic_id: int = Field(foreign_key="topic.id", index=True)
    summary: str
    sentiment: Optional[str] = Field(default=None, max_length=20)  # bullish/bearish/neutral
    key_levels: Optional[str] = Field(default=None)  # JSON string: ["4800", "4400"]
    start_time: Optional[str] = Field(default=None, max_length=20)
    end_time: Optional[str] = Field(default=None, max_length=20)
    confidence: float = Field(default=1.0)
    created_at: datetime = Field(default_factory=utc_now)
