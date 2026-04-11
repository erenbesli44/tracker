from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class Topic(SQLModel, table=True):
    __tablename__ = "topic"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255, unique=True, index=True)
    parent_id: Optional[int] = Field(default=None, foreign_key="topic.id", index=True)
    description: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
