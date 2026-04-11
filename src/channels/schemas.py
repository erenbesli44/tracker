from datetime import datetime
from typing import Optional

from pydantic import Field

from src.models import CustomModel


class ChannelCreate(CustomModel):
    name: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="youtube", max_length=50)
    channel_handle: Optional[str] = Field(default=None, max_length=255)
    youtube_channel_id: Optional[str] = Field(default=None, max_length=100)
    channel_url: Optional[str] = Field(default=None, max_length=500)
    bio: Optional[str] = None
    legacy_person_id: Optional[int] = Field(default=None, ge=1)


class ChannelUpdate(CustomModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    platform: Optional[str] = Field(default=None, max_length=50)
    channel_handle: Optional[str] = Field(default=None, max_length=255)
    youtube_channel_id: Optional[str] = Field(default=None, max_length=100)
    channel_url: Optional[str] = Field(default=None, max_length=500)
    bio: Optional[str] = None


class ChannelResponse(CustomModel):
    id: int
    name: str
    slug: str
    platform: str
    channel_handle: Optional[str]
    youtube_channel_id: Optional[str]
    channel_url: Optional[str]
    bio: Optional[str]
    legacy_person_id: Optional[int]
    created_at: datetime
    updated_at: Optional[datetime]
