import json
from datetime import datetime, timedelta, timezone

from slugify import slugify
from sqlalchemy import func
from sqlmodel import Session, select

from src.channels.models import YouTubeChannel
from src.classification.models import TopicMention
from src.topics.models import Topic
from src.topics.schemas import (
    ChannelOpinionEntry,
    ChannelOpinionGroup,
    TopicCreate,
    TopicNode,
    TopicOpinionsResponse,
    TopicResponse,
)
from src.videos.models import Video


def create(session: Session, data: TopicCreate) -> Topic:
    slug = slugify(data.name)
    topic = Topic(
        name=data.name,
        slug=slug,
        parent_id=data.parent_id,
        description=data.description,
    )
    session.add(topic)
    session.commit()
    session.refresh(topic)
    return topic


def get_by_id(session: Session, topic_id: int) -> Topic | None:
    return session.get(Topic, topic_id)


def get_by_slug(session: Session, slug: str) -> Topic | None:
    return session.exec(select(Topic).where(Topic.slug == slug)).first()


def list_all(session: Session) -> list[Topic]:
    return list(session.exec(select(Topic).order_by(Topic.parent_id.asc(), Topic.name)).all())


def list_main_topics(session: Session) -> list[Topic]:
    return list(
        session.exec(
            select(Topic).where(Topic.parent_id == None).order_by(Topic.name)  # noqa: E711
        ).all()
    )


def list_sub_topics(session: Session, parent_id: int) -> list[Topic]:
    return list(
        session.exec(
            select(Topic).where(Topic.parent_id == parent_id).order_by(Topic.name)
        ).all()
    )


def get_topic_tree(session: Session) -> list[TopicNode]:
    main_topics = list_main_topics(session)
    tree = []
    for main in main_topics:
        sub_topics = list_sub_topics(session, main.id)
        node = TopicNode(
            id=main.id,
            name=main.name,
            slug=main.slug,
            description=main.description,
            sub_topics=[
                TopicNode(id=s.id, name=s.name, slug=s.slug, description=s.description)
                for s in sub_topics
            ],
        )
        tree.append(node)
    return tree


def seed_topics(session: Session, seed_data: list[dict]) -> None:
    """Idempotent seed — skips topics that already exist by slug."""
    # First pass: create main topics (parent_slug == None)
    for item in seed_data:
        if item["parent_slug"] is None:
            if not get_by_slug(session, item["slug"]):
                topic = Topic(
                    name=item["name"],
                    slug=item["slug"],
                    parent_id=None,
                    description=item.get("description"),
                )
                session.add(topic)
    session.commit()

    # Second pass: create sub-topics (resolve parent slug → id)
    for item in seed_data:
        if item["parent_slug"] is not None:
            if not get_by_slug(session, item["slug"]):
                parent = get_by_slug(session, item["parent_slug"])
                if parent:
                    topic = Topic(
                        name=item["name"],
                        slug=item["slug"],
                        parent_id=parent.id,
                        description=item.get("description"),
                    )
                    session.add(topic)
    session.commit()


def _parse_key_levels(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def get_topic_opinions_by_channel(
    session: Session,
    topic: Topic,
    limit_per_channel: int = 5,
    days: int | None = 30,
) -> TopicOpinionsResponse:
    cutoff = None
    if days is not None:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # Use published_at when available; fall back to mention created_at.
    sort_ts = func.coalesce(Video.published_at, TopicMention.created_at)

    # Query 1: count mentions per channel, ordered by latest activity datetime
    count_stmt = (
        select(TopicMention.channel_id, func.count(TopicMention.id).label("cnt"))
        .join(Video, TopicMention.video_id == Video.id)
        .where(TopicMention.topic_id == topic.id)
        .where(TopicMention.channel_id.isnot(None))
    )
    if cutoff:
        count_stmt = count_stmt.where(TopicMention.created_at >= cutoff)
    count_stmt = count_stmt.group_by(TopicMention.channel_id).order_by(
        func.max(sort_ts).desc()
    )
    count_rows = session.execute(count_stmt).all()

    if not count_rows:
        parent_name = None
        if topic.parent_id:
            parent = session.get(Topic, topic.parent_id)
            parent_name = parent.name if parent else None
        return TopicOpinionsResponse(
            topic=TopicResponse(
                id=topic.id, name=topic.name, slug=topic.slug,
                parent_id=topic.parent_id, description=topic.description,
                created_at=topic.created_at,
            ),
            total_channels=0,
            channel_opinions=[],
        )

    channel_ids = [row[0] for row in count_rows]
    counts_by_channel = {row[0]: row[1] for row in count_rows}

    # Query 2: fetch mentions + videos for all channels
    mentions_stmt = (
        select(TopicMention, Video)
        .join(Video, TopicMention.video_id == Video.id)
        .where(TopicMention.topic_id == topic.id)
        .where(TopicMention.channel_id.in_(channel_ids))
    )
    if cutoff:
        mentions_stmt = mentions_stmt.where(TopicMention.created_at >= cutoff)
    mentions_stmt = mentions_stmt.order_by(
        TopicMention.channel_id,
        sort_ts.desc(),
        TopicMention.id.desc(),
    )
    mention_rows = session.execute(mentions_stmt).all()

    # Group by channel and limit per channel
    grouped: dict[int, list[ChannelOpinionEntry]] = {}
    for mention, video in mention_rows:
        cid = mention.channel_id
        if cid not in grouped:
            grouped[cid] = []
        if len(grouped[cid]) < limit_per_channel:
            grouped[cid].append(
                ChannelOpinionEntry(
                    mention_id=mention.id,
                    video_id=video.id,
                    video_title=video.title,
                    video_url=video.video_url,
                    published_at=video.published_at,
                    summary=mention.summary,
                    sentiment=mention.sentiment,
                    key_levels=_parse_key_levels(mention.key_levels),
                    confidence=mention.confidence,
                )
            )

    # Query 3: batch-load channel details
    channels_stmt = select(YouTubeChannel).where(YouTubeChannel.id.in_(channel_ids))
    channels = {ch.id: ch for ch in session.exec(channels_stmt).all()}

    # Build response in the same order as count_rows (most recent first)
    parent_name = None
    if topic.parent_id:
        parent = session.get(Topic, topic.parent_id)
        parent_name = parent.name if parent else None

    channel_opinions = []
    for cid in channel_ids:
        ch = channels.get(cid)
        entries = grouped.get(cid, [])
        channel_opinions.append(
            ChannelOpinionGroup(
                channel_id=cid,
                channel_name=ch.name if ch else "Unknown",
                channel_slug=ch.slug if ch else "",
                channel_handle=ch.channel_handle if ch else None,
                mention_count=counts_by_channel[cid],
                latest_sentiment=entries[0].sentiment if entries else None,
                entries=entries,
            )
        )

    return TopicOpinionsResponse(
        topic=TopicResponse(
            id=topic.id, name=topic.name, slug=topic.slug,
            parent_id=topic.parent_id, description=topic.description,
            created_at=topic.created_at,
        ),
        total_channels=len(channel_ids),
        channel_opinions=channel_opinions,
    )
