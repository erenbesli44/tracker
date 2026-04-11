from typing import Literal, Optional

from pydantic import Field

from src.models import CustomModel


class TopicMentionCreate(CustomModel):
    topic_id: int = Field(ge=1)
    summary: str = Field(min_length=1)
    sentiment: Optional[Literal["bullish", "bearish", "neutral"]] = None
    key_levels: Optional[list[str]] = None
    start_time: Optional[str] = Field(default=None, max_length=20)
    end_time: Optional[str] = Field(default=None, max_length=20)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ClassificationRequest(CustomModel):
    topic_mentions: list[TopicMentionCreate] = Field(min_length=1)


class TopicMentionResponse(CustomModel):
    id: int
    video_id: int
    channel_id: Optional[int]
    person_id: Optional[int]
    topic_id: int
    summary: str
    sentiment: Optional[str]
    key_levels: Optional[list[str]]
    start_time: Optional[str]
    end_time: Optional[str]
    confidence: float


class ClassificationResponse(CustomModel):
    video_id: int
    total_mentions: int
    mentions: list[TopicMentionResponse]


class PromptTemplateResponse(CustomModel):
    system_prompt: str
    user_prompt_template: str
