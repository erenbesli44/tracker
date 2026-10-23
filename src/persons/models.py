from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class Person(SQLModel, table=True):
    __tablename__ = "person"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255, unique=True, index=True)
    platform: str = Field(default="youtube", max_length=50)
    platform_handle: Optional[str] = Field(default=None, max_length=255)
    bio: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = Field(default=None)


class VideoParticipants(SQLModel, table=True):
    """Guests detected in a multi-guest channel's video (one row per video).

    participants_json: [{"person_id": 9, "name": "Atilla Yeşilada", "role": "guest"}, ...]
    plus the host as role "host" when known (host views are never attributed).
    """

    __tablename__ = "video_participants"

    video_id: int = Field(primary_key=True, foreign_key="video.id")
    participants_json: str = Field(default="[]")
    version: str = Field(max_length=20)
    created_at: datetime = Field(default_factory=utc_now)
