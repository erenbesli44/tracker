"""SQLModel tables for the Twitter posting job."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class TwitterPostRun(SQLModel, table=True):
    __tablename__ = "twitter_post_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = Field(default=None)
    # running / success / partial_fail / failed
    status: str = Field(default="running", max_length=20)
    candidates_found: int = Field(default=0)
    posted: int = Field(default=0)
    skipped: int = Field(default=0)
    failed: int = Field(default=0)
    error_details: Optional[str] = Field(default=None)  # JSON array of error strings


class TwitterPost(SQLModel, table=True):
    """One row per video that the bot has attempted (or intentionally skipped).

    Unique on video_id: the bot never posts the same video twice. To retry a
    failed post, delete the row manually.
    """

    __tablename__ = "twitter_post"

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", unique=True, index=True)
    run_id: Optional[int] = Field(default=None, foreign_key="twitter_post_run.id", index=True)
    # posted / failed / skipped
    status: str = Field(default="posted", max_length=20)
    tweet_id: Optional[str] = Field(default=None, max_length=50)
    tweet_url: Optional[str] = Field(default=None, max_length=255)
    tweet_text: Optional[str] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    attempt_count: int = Field(default=0)
    thread_length: int = Field(default=1)  # number of tweets in the thread
    thread_tweet_ids: Optional[str] = Field(default=None)  # JSON array of all tweet IDs
    created_at: datetime = Field(default_factory=utc_now)
    posted_at: Optional[datetime] = Field(default=None)
