import json

from sqlmodel import Session, select

from src.classification.exceptions import ClassificationNotFound
from src.classification.models import TopicMention
from src.classification.schemas import (
    ClassificationRequest,
    ClassificationResponse,
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

    mentions = []
    for item in data.topic_mentions:
        mention = TopicMention(
            video_id=video.id,
            channel_id=video.channel_id,
            person_id=video.person_id,
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
