from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class Video(SQLModel, table=True):
    __tablename__ = "video"

    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: Optional[int] = Field(default=None, foreign_key="youtube_channel.id", index=True)
    person_id: Optional[int] = Field(default=None, foreign_key="person.id", index=True)
    platform: str = Field(default="youtube", max_length=50)
    video_url: str = Field(max_length=500, unique=True)
    video_id: str = Field(max_length=100, index=True)
    title: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = Field(default=None)
    duration: Optional[int] = Field(default=None)  # seconds
    created_at: datetime = Field(default_factory=utc_now)


class Transcript(SQLModel, table=True):
    __tablename__ = "transcript"

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", unique=True, index=True)
    raw_text: str
    segments_json: Optional[str] = Field(default=None)  # JSON string of transcript segments
    language: str = Field(default="tr", max_length=10)
    created_at: datetime = Field(default_factory=utc_now)


class VideoSummary(SQLModel, table=True):
    __tablename__ = "video_summary"

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", unique=True, index=True)
    short_summary: str
    long_summary: Optional[str] = Field(default=None)
    highlights: Optional[str] = Field(default=None)  # JSON string array
    language: str = Field(default="tr", max_length=10)
    source: str = Field(default="manual", max_length=20)  # manual | llm
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = Field(default=None)
