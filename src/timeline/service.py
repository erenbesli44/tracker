import json

from sqlalchemy import func
from sqlmodel import Session, select

from src.channels import service as channels_service
from src.classification.models import TopicMention
from src.timeline.schemas import (
    PersonOverviewResponse,
    PersonTopicSummary,
    TimelineEntry,
    TimelineResponse,
    TopicInfo,
)
from src.topics.models import Topic
from src.videos.models import Video


def _parse_key_levels(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _load_parent_names(session: Session, parent_ids: set[int]) -> dict[int, str]:
    """Batch-load parent topic names to avoid per-topic queries."""
    if not parent_ids:
        return {}
    rows = session.exec(
        select(Topic.id, Topic.name).where(Topic.id.in_(parent_ids))
    ).all()
    return {row[0]: row[1] for row in rows}


def _build_topic_info(
    topic: Topic,
    parent_names: dict[int, str],
) -> TopicInfo:
    return TopicInfo(
        id=topic.id,
        name=topic.name,
        slug=topic.slug,
        parent_id=topic.parent_id,
        parent_name=parent_names.get(topic.parent_id) if topic.parent_id else None,
    )


def get_topic_timeline(
    session: Session,
    person_id: int,
    topic_id: int,
    limit: int = 50,
    offset: int = 0,
) -> TimelineResponse | None:
    topic = session.get(Topic, topic_id)
    if not topic:
        return None

    parent_names = _load_parent_names(
        session, {topic.parent_id} if topic.parent_id else set()
    )

    stmt = (
        select(TopicMention, Video)
        .join(Video, TopicMention.video_id == Video.id)
        .where(TopicMention.person_id == person_id)
        .where(TopicMention.topic_id == topic_id)
        .order_by(Video.published_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    rows = session.execute(stmt).all()

    total_stmt = (
        select(func.count(TopicMention.id))
        .where(TopicMention.person_id == person_id)
        .where(TopicMention.topic_id == topic_id)
    )
    total = session.execute(total_stmt).scalar_one()

    entries = [
        TimelineEntry(
            mention_id=mention.id,
            video_id=video.id,
            video_title=video.title,
            video_url=video.video_url,
            published_at=video.published_at,
            summary=mention.summary,
            sentiment=mention.sentiment,
            key_levels=_parse_key_levels(mention.key_levels),
            start_time=mention.start_time,
            end_time=mention.end_time,
            confidence=mention.confidence,
        )
        for mention, video in rows
    ]

    channel = channels_service.get_by_legacy_person_id(session, person_id)

    return TimelineResponse(
        topic=_build_topic_info(topic, parent_names),
        channel_id=channel.id if channel else None,
        person_id=person_id,
        total=total,
        entries=entries,
    )


def _build_overview(
    session: Session,
    filter_column,
    filter_value: int,
) -> list[PersonTopicSummary]:
    """Build topic overview for a person or channel in 3 queries (no N+1).

    1. Count mentions per topic
    2. Batch-load parent topic names
    3. Fetch latest mention per topic using a window function
    """
    # Query 1: counts + topic objects
    count_stmt = (
        select(Topic, func.count(TopicMention.id).label("mention_count"))
        .join(TopicMention, Topic.id == TopicMention.topic_id)
        .where(filter_column == filter_value)
        .group_by(Topic.id)
        .order_by(func.count(TopicMention.id).desc())
    )
    count_rows = session.execute(count_stmt).all()
    if not count_rows:
        return []

    topics_by_id: dict[int, Topic] = {}
    counts_by_topic: dict[int, int] = {}
    parent_ids: set[int] = set()
    for topic, count in count_rows:
        topics_by_id[topic.id] = topic
        counts_by_topic[topic.id] = count
        if topic.parent_id:
            parent_ids.add(topic.parent_id)

    # Query 2: batch-load parent names
    parent_names = _load_parent_names(session, parent_ids)

    # Query 3: latest mention per topic in a single query
    # Use a subquery to find the max published_at per topic, then join back.
    topic_ids = list(topics_by_id.keys())
    latest_stmt = (
        select(TopicMention, Video)
        .join(Video, TopicMention.video_id == Video.id)
        .where(filter_column == filter_value)
        .where(TopicMention.topic_id.in_(topic_ids))
        .order_by(
            TopicMention.topic_id,
            Video.published_at.desc().nullslast(),
        )
    )
    latest_rows = session.execute(latest_stmt).all()

    # Keep only the first (latest) row per topic_id
    latest_by_topic: dict[int, tuple] = {}
    for mention, video in latest_rows:
        if mention.topic_id not in latest_by_topic:
            latest_by_topic[mention.topic_id] = (mention, video)

    # Assemble results in the same order as count_rows
    summaries: list[PersonTopicSummary] = []
    for topic, _count in count_rows:
        latest_sentiment = None
        latest_summary = ""
        latest_published_at = None
        latest_video_url = None

        latest = latest_by_topic.get(topic.id)
        if latest:
            mention, video = latest
            latest_sentiment = mention.sentiment
            latest_summary = mention.summary
            latest_published_at = video.published_at
            latest_video_url = video.video_url

        summaries.append(
            PersonTopicSummary(
                topic=_build_topic_info(topic, parent_names),
                mention_count=counts_by_topic[topic.id],
                latest_sentiment=latest_sentiment,
                latest_summary=latest_summary,
                latest_published_at=latest_published_at,
                latest_video_url=latest_video_url,
            )
        )

    return summaries


def get_person_overview(session: Session, person_id: int) -> PersonOverviewResponse:
    summaries = _build_overview(session, TopicMention.person_id, person_id)
    channel = channels_service.get_by_legacy_person_id(session, person_id)
    return PersonOverviewResponse(
        channel_id=channel.id if channel else None,
        person_id=person_id,
        topics=summaries,
    )


def get_channel_topic_timeline(
    session: Session,
    channel_id: int,
    topic_id: int,
    limit: int = 50,
    offset: int = 0,
) -> TimelineResponse | None:
    topic = session.get(Topic, topic_id)
    if not topic:
        return None

    channel = channels_service.get_by_id(session, channel_id)
    if not channel:
        return None

    parent_names = _load_parent_names(
        session, {topic.parent_id} if topic.parent_id else set()
    )

    stmt = (
        select(TopicMention, Video)
        .join(Video, TopicMention.video_id == Video.id)
        .where(TopicMention.channel_id == channel_id)
        .where(TopicMention.topic_id == topic_id)
        .order_by(Video.published_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
    )
    rows = session.execute(stmt).all()

    total_stmt = (
        select(func.count(TopicMention.id))
        .where(TopicMention.channel_id == channel_id)
        .where(TopicMention.topic_id == topic_id)
    )
    total = session.execute(total_stmt).scalar_one()

    entries = [
        TimelineEntry(
            mention_id=mention.id,
            video_id=video.id,
            video_title=video.title,
            video_url=video.video_url,
            published_at=video.published_at,
            summary=mention.summary,
            sentiment=mention.sentiment,
            key_levels=_parse_key_levels(mention.key_levels),
            start_time=mention.start_time,
            end_time=mention.end_time,
            confidence=mention.confidence,
        )
        for mention, video in rows
    ]

    return TimelineResponse(
        topic=_build_topic_info(topic, parent_names),
        channel_id=channel_id,
        person_id=channel.legacy_person_id,
        total=total,
        entries=entries,
    )


def get_channel_overview(session: Session, channel_id: int) -> PersonOverviewResponse:
    channel = channels_service.get_by_id(session, channel_id)
    if not channel:
        raise ValueError("Channel not found")

    summaries = _build_overview(session, TopicMention.channel_id, channel_id)
    return PersonOverviewResponse(
        channel_id=channel_id,
        person_id=channel.legacy_person_id,
        topics=summaries,
    )
