from datetime import datetime
from typing import Optional

from pydantic import Field, field_validator

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
