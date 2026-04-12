from datetime import datetime
from typing import Literal, Optional

from pydantic import Field, model_validator

from src.models import CustomModel


class VideoCreate(CustomModel):
    person_id: Optional[int] = Field(default=None, ge=1)
    channel_id: Optional[int] = Field(default=None, ge=1)
    video_url: str = Field(min_length=1, max_length=500)
    title: Optional[str] = Field(default=None, max_length=500)
    published_at: Optional[datetime] = None
    duration: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_owner(self) -> "VideoCreate":
        if self.person_id is None and self.channel_id is None:
            raise ValueError("Either person_id or channel_id must be provided")
        return self


class VideoResponse(CustomModel):
    id: int
    person_id: Optional[int]
    channel_id: Optional[int]
    platform: str
    video_url: str
    video_id: str
    title: Optional[str]
    published_at: Optional[datetime]
    duration: Optional[int]
    created_at: datetime


class VideoUpdate(CustomModel):
    title: Optional[str] = None
    published_at: Optional[datetime] = None
    duration: Optional[int] = Field(default=None, ge=0)


class TranscriptCreate(CustomModel):
    raw_text: str = Field(min_length=1)
    language: str = Field(default="tr", max_length=10)


class TranscriptResponse(CustomModel):
    id: int
    video_id: int
    language: str
    created_at: datetime
    # raw_text intentionally excluded from default response (can be large)


class TranscriptDetailResponse(TranscriptResponse):
    raw_text: str


class TranscriptFetchRequest(CustomModel):
    languages: Optional[list[str]] = None
    overwrite_existing: bool = False


class TranscriptFetchResponse(CustomModel):
    transcript: TranscriptDetailResponse
    source: str
    fetched_language: str
    is_generated: Optional[bool]
    segment_count: int
    languages_tried: list[str]


class VideoSummaryCreate(CustomModel):
    short_summary: str = Field(min_length=1)
    long_summary: Optional[str] = None
    highlights: Optional[list[str]] = None
    language: str = Field(default="tr", max_length=10)
    source: Literal["manual", "llm"] = "manual"


class VideoSummaryResponse(CustomModel):
    id: int
    video_id: int
    short_summary: str
    long_summary: Optional[str]
    highlights: Optional[list[str]]
    language: str
    source: str
    created_at: datetime
    updated_at: Optional[datetime]


class VideoWithTranscript(VideoResponse):
    transcript: Optional[TranscriptResponse] = None
