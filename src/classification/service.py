import json

from sqlmodel import Session, select

from src.classification.exceptions import ClassificationNotFound
from src.classification.models import TopicMention
from src.classification.schemas import (
    ClassificationRequest,
    ClassificationResponse,
    TopicMentionCreate,
    TopicMentionResponse,
)
from src.videos.models import Video


def _parse_key_levels(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _mention_to_response(mention: TopicMention) -> TopicMentionResponse:
    return TopicMentionResponse(
        id=mention.id,
        video_id=mention.video_id,
        channel_id=mention.channel_id,
        person_id=mention.person_id,
        topic_id=mention.topic_id,
        summary=mention.summary,
        sentiment=mention.sentiment,
        key_levels=_parse_key_levels(mention.key_levels),
        start_time=mention.start_time,
        end_time=mention.end_time,
        confidence=mention.confidence,
    )


def mention_person_id(session: Session, video: Video) -> int | None:
    """Person a new mention of this video belongs to.

    Multi-guest channels: the speaker of each view is set later by the outlook
    step (src/persons/participants.py), never the video's headline guest.
    """
    if video.channel_id is not None:
        from src.channels.models import YouTubeChannel
        from src.persons import detector

        channel = session.get(YouTubeChannel, video.channel_id)
        if channel is not None and detector.is_multi_guest_channel(channel.channel_handle):
            return None
    return video.person_id


def add_mentions(
    session: Session, video: Video, items: list[TopicMentionCreate]
) -> list[TopicMention]:
    """Add mention rows for a video (not committed); existing rows are left alone."""
    person_id = mention_person_id(session, video)
    mentions = []
    for item in items:
        mention = TopicMention(
            video_id=video.id,
            channel_id=video.channel_id,
            person_id=person_id,
            topic_id=item.topic_id,
            summary=item.summary,
            sentiment=item.sentiment,
            key_levels=json.dumps(item.key_levels) if item.key_levels else None,
            start_time=item.start_time,
            end_time=item.end_time,
            confidence=item.confidence,
        )
        session.add(mention)
        mentions.append(mention)
    return mentions


def classify_video(
    session: Session,
    video: Video,
    data: ClassificationRequest,
) -> ClassificationResponse:
    # Delete existing mentions (re-classification replaces previous results)
    existing = list(
        session.exec(select(TopicMention).where(TopicMention.video_id == video.id)).all()
    )
    for mention in existing:
        session.delete(mention)
    session.flush()

    mentions = add_mentions(session, video, data.topic_mentions)

    session.commit()
    for m in mentions:
        session.refresh(m)

    return ClassificationResponse(
        video_id=video.id,
        total_mentions=len(mentions),
        mentions=[_mention_to_response(m) for m in mentions],
    )


def get_classification(session: Session, video_id: int) -> ClassificationResponse:
    mentions = list(
        session.exec(select(TopicMention).where(TopicMention.video_id == video_id)).all()
    )
    if not mentions:
        raise ClassificationNotFound(video_id)
    return ClassificationResponse(
        video_id=video_id,
        total_mentions=len(mentions),
        mentions=[_mention_to_response(m) for m in mentions],
    )
