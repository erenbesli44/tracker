import json
from datetime import UTC, datetime

from slugify import slugify
from sqlmodel import Session, select

from src.channels.models import YouTubeChannel
from src.channels.schemas import ChannelCreate, ChannelUpdate
from src.persons.models import Person


def _encode_subtopics(subtopics: list[str] | None) -> str | None:
    if subtopics is None:
        return None
    return json.dumps(subtopics, ensure_ascii=False)


def decode_subtopics(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else None
    except (json.JSONDecodeError, TypeError):
        return None


def create(session: Session, data: ChannelCreate) -> YouTubeChannel:
    slug = slugify(data.name)
    channel = YouTubeChannel(
        name=data.name,
        slug=slug,
        platform=data.platform,
        channel_handle=data.channel_handle,
        youtube_channel_id=data.youtube_channel_id,
        channel_url=data.channel_url,
        bio=data.bio,
        primary_topic_slug=data.primary_topic_slug,
        expected_subtopics=_encode_subtopics(data.expected_subtopics),
        legacy_person_id=data.legacy_person_id,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def get_by_id(session: Session, channel_id: int) -> YouTubeChannel | None:
    return session.get(YouTubeChannel, channel_id)


def get_by_slug(session: Session, slug: str) -> YouTubeChannel | None:
    return session.exec(select(YouTubeChannel).where(YouTubeChannel.slug == slug)).first()


def get_by_channel_handle(session: Session, channel_handle: str) -> YouTubeChannel | None:
    return session.exec(
        select(YouTubeChannel).where(YouTubeChannel.channel_handle == channel_handle)
    ).first()


def get_by_youtube_channel_id(session: Session, youtube_channel_id: str) -> YouTubeChannel | None:
    return session.exec(
        select(YouTubeChannel).where(YouTubeChannel.youtube_channel_id == youtube_channel_id)
    ).first()


def get_by_legacy_person_id(session: Session, person_id: int) -> YouTubeChannel | None:
    return session.exec(
        select(YouTubeChannel).where(YouTubeChannel.legacy_person_id == person_id)
    ).first()


def list_all(session: Session) -> list[YouTubeChannel]:
    return list(session.exec(select(YouTubeChannel).order_by(YouTubeChannel.name)).all())


def update(session: Session, channel: YouTubeChannel, data: ChannelUpdate) -> YouTubeChannel:
    update_data = data.model_dump(exclude_unset=True)
    if "expected_subtopics" in update_data:
        update_data["expected_subtopics"] = _encode_subtopics(update_data["expected_subtopics"])
    for key, value in update_data.items():
        setattr(channel, key, value)

    if "name" in update_data:
        channel.slug = slugify(channel.name)

    channel.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def ensure_from_person(session: Session, person: Person) -> YouTubeChannel:
    existing = get_by_legacy_person_id(session, person.id)
    if existing:
        updated = False
        if not existing.channel_handle and person.platform_handle:
            existing.channel_handle = person.platform_handle
            updated = True
        if not existing.bio and person.bio:
            existing.bio = person.bio
            updated = True
        if updated:
            existing.updated_at = datetime.now(UTC).replace(tzinfo=None)
            session.add(existing)
            session.commit()
            session.refresh(existing)
        return existing

    by_handle = None
    if person.platform_handle:
        by_handle = get_by_channel_handle(session, person.platform_handle)
    if by_handle:
        by_handle.legacy_person_id = person.id
        by_handle.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(by_handle)
        session.commit()
        session.refresh(by_handle)
        return by_handle

    return create(
        session,
        ChannelCreate(
            name=person.name,
            platform=person.platform,
            channel_handle=person.platform_handle,
            bio=person.bio,
            legacy_person_id=person.id,
        ),
    )
