from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from src.classification.schemas import TopicMentionCreate
from src.models import CustomModel


class IngestionPersonInput(CustomModel):
    id: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    platform: str = Field(default="youtube", max_length=50)
    platform_handle: str | None = Field(default=None, max_length=255)
    bio: str | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> "IngestionPersonInput":
        if self.id is None and not self.name:
            raise ValueError("Either person.id or person.name must be provided")
        return self


class IngestionVideoInput(CustomModel):
    video_url: str = Field(min_length=1, max_length=500)
    title: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    duration: int | None = Field(default=None, ge=0)


class IngestionTranscriptInput(CustomModel):
    raw_text: str = Field(min_length=1)
    language: str = Field(default="tr", max_length=10)


class IngestionSummaryInput(CustomModel):
    short_summary: str = Field(min_length=1)
    long_summary: str | None = None
    highlights: list[str] | None = None
    language: str = Field(default="tr", max_length=10)
    source: Literal["manual", "llm"] = "manual"


class IngestionClassificationInput(CustomModel):
    source: Literal["manual", "llm"] = "manual"
    topic_mentions: list[TopicMentionCreate] = Field(min_length=1)


class IngestionOverwriteFlags(CustomModel):
    transcript: bool = False
    summary: bool = True
    classification: bool = True


class IngestionYoutubeRequest(CustomModel):
    person: IngestionPersonInput | None = None
    video: IngestionVideoInput
    transcript: IngestionTranscriptInput | None = None
    summary: IngestionSummaryInput | None = None
    classification: IngestionClassificationInput | None = None
    overwrite: IngestionOverwriteFlags = Field(default_factory=IngestionOverwriteFlags)
    transcript_languages: list[str] | None = None
    request_id: str | None = Field(default=None, max_length=100)


class IngestionYoutubeChannelRunRequest(CustomModel):
    youtube_channel: str = Field(min_length=1, max_length=500)
    video_count: int = Field(default=3, ge=1, le=20)
    transcript_languages: list[str] | None = None


class IngestionActionResponse(CustomModel):
    person: Literal["created", "reused", "skipped"]
    channel: Literal["created", "reused"]
    video: Literal["created", "reused"]
    transcript: Literal["created", "updated", "skipped"]
    summary: Literal["created", "updated", "skipped"]
    classification: Literal["created", "replaced", "skipped"]


class IngestionYoutubeResponse(CustomModel):
    status: Literal["stored", "unchanged"]
    person_id: int | None = None
    channel_id: int
    video_id: int
    transcript_id: int | None = None
    summary_id: int | None = None
    classification_mentions: int = 0
    actions: IngestionActionResponse


class IngestionYoutubeChannelRunVideoResult(CustomModel):
    youtube_video_id: str
    video_url: str
    status: Literal[
        "ingested",
        "skipped_existing",
        "skipped_transcript_unavailable",
        "failed",
    ]
    video_id: int | None = None
    detail: str | None = None


class IngestionYoutubeChannelRunResponse(CustomModel):
    status: Literal["completed", "partial"]
    youtube_channel_id: str
    requested_video_count: int
    videos_detected: int
    videos_ingested: int
    videos_skipped_existing: int
    videos_skipped_no_transcript: int
    errors_count: int
    results: list[IngestionYoutubeChannelRunVideoResult]
