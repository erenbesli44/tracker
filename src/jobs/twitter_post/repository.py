"""Persistence helpers for the Twitter posting job."""

from datetime import datetime

from sqlalchemy import func
from sqlmodel import Session, select

from src.channels.models import YouTubeChannel
from src.classification.models import TopicMention
from src.jobs.twitter_post.models import TwitterPost, TwitterPostRun
from src.models import utc_now
from src.topics.models import Topic
from src.videos.models import Video, VideoSummary


def create_run(session: Session) -> TwitterPostRun:
    run = TwitterPostRun()
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def finalize_run(
    session: Session,
    run: TwitterPostRun,
    *,
    status: str,
    candidates_found: int,
    posted: int,
    skipped: int,
    failed: int,
    error_details: str | None,
) -> TwitterPostRun:
    run.finished_at = utc_now()
    run.status = status
    run.candidates_found = candidates_found
    run.posted = posted
    run.skipped = skipped
    run.failed = failed
    run.error_details = error_details
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def find_unposted_summaries(
    session: Session,
    *,
    last_n: int = 10,
) -> list[tuple[Video, VideoSummary]]:
    """Return unposted (Video, VideoSummary) pairs from the last N videos by recency.

    Sorts by COALESCE(published_at, created_at) DESC so newly-ingested videos
    surface even when yt-dlp's extract_flat returned no upload_date and
    published_at is still NULL. A plain `published_at DESC NULLS LAST` would
    push undated-but-fresh videos behind the limit window — exactly the
    starvation scenario that broke the bot when 30+ NULL-dated videos piled up.
    """
    posted_ids = set(session.exec(select(TwitterPost.video_id)).all())
    recency = func.coalesce(Video.published_at, Video.created_at)
    recent = session.exec(
        select(Video, VideoSummary)
        .join(VideoSummary, VideoSummary.video_id == Video.id)
        .order_by(recency.desc(), Video.created_at.desc())
        .limit(last_n)
    ).all()
    return [(v, s) for v, s in recent if v.id not in posted_ids]


def get_channel(session: Session, channel_id: int | None) -> YouTubeChannel | None:
    if channel_id is None:
        return None
    return session.get(YouTubeChannel, channel_id)


def get_topic_mentions(
    session: Session, video_id: int
) -> list[tuple[TopicMention, Topic]]:
    """Return TopicMentions with their Topic rows, sorted by confidence desc."""
    rows = session.exec(
        select(TopicMention, Topic)
        .join(Topic, Topic.id == TopicMention.topic_id)
        .where(TopicMention.video_id == video_id)
        .order_by(TopicMention.confidence.desc())
    ).all()
    return list(rows)


def record_post(
    session: Session,
    *,
    run_id: int,
    video_id: int,
    status: str,
    tweet_id: str | None,
    tweet_url: str | None,
    tweet_text: str | None,
    thread_tweet_ids: str | None,
    thread_length: int,
    error_message: str | None,
    attempt_count: int,
    posted_at: datetime | None,
) -> TwitterPost:
    post = TwitterPost(
        run_id=run_id,
        video_id=video_id,
        status=status,
        tweet_id=tweet_id,
        tweet_url=tweet_url,
        tweet_text=tweet_text,
        thread_tweet_ids=thread_tweet_ids,
        thread_length=thread_length,
        error_message=error_message,
        attempt_count=attempt_count,
        posted_at=posted_at,
    )
    session.add(post)
    session.commit()
    session.refresh(post)
    return post
