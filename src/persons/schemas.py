from datetime import datetime
from typing import Optional

from pydantic import Field

from src.models import CustomModel


class PersonCreate(CustomModel):
    name: str = Field(min_length=1, max_length=255)
    platform: str = Field(default="youtube", max_length=50)
    platform_handle: Optional[str] = None
    bio: Optional[str] = None


class PersonUpdate(CustomModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    platform: Optional[str] = Field(default=None, max_length=50)
    platform_handle: Optional[str] = None
    bio: Optional[str] = None


class PersonResponse(CustomModel):
    id: int
    name: str
    slug: str
    platform: str
    platform_handle: Optional[str]
    bio: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime]
