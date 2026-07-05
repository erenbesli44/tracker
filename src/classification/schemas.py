from typing import Literal

from pydantic import Field

from src.models import CustomModel


class TopicMentionCreate(CustomModel):
    topic_id: int = Field(ge=1)
    summary: str = Field(min_length=1)
    sentiment: Literal["bullish", "bearish", "neutral", "positive", "negative"] | None = None
    key_levels: list[str] | None = None
    start_time: str | None = Field(default=None, max_length=20)
    end_time: str | None = Field(default=None, max_length=20)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ClassificationRequest(CustomModel):
    topic_mentions: list[TopicMentionCreate] = Field(min_length=1)


class TopicMentionResponse(CustomModel):
    id: int
    video_id: int
    channel_id: int | None
    person_id: int | None
    topic_id: int
    summary: str
    sentiment: str | None
    key_levels: list[str] | None
    start_time: str | None
    end_time: str | None
    confidence: float


class ClassificationResponse(CustomModel):
    video_id: int
    total_mentions: int
    mentions: list[TopicMentionResponse]


class PromptTemplateResponse(CustomModel):
    system_prompt: str
    user_prompt_template: str
