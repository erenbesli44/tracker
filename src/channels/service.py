import json
from datetime import UTC, datetime
from typing import Any

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


def _encode_channel_metadata(metadata: dict[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    return json.dumps(metadata, ensure_ascii=False)


def decode_channel_metadata(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
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
        channel_metadata=_encode_channel_metadata(data.channel_metadata),
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
    if "channel_metadata" in update_data:
        update_data["channel_metadata"] = _encode_channel_metadata(update_data["channel_metadata"])
    for key, value in update_data.items():
        setattr(channel, key, value)

    if "name" in update_data:
        channel.slug = slugify(channel.name)

    channel.updated_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return channel


def upsert_profile_metadata(
    session: Session,
    channel: YouTubeChannel,
    profile: dict[str, Any],
    *,
    fill_bio: bool = True,
    fill_channel_url: bool = True,
    fill_youtube_channel_id: bool = True,
) -> YouTubeChannel:
    """Merge yt-dlp-derived profile data into a channel row.

    ``profile`` is the dict produced by ``extract_channel_profile_from_info``.
    Non-destructive for existing values — only fills missing scalar fields.
    """
    changed = False

    existing = decode_channel_metadata(channel.channel_metadata) or {}
    merged = {**existing, **{k: v for k, v in profile.items() if v is not None}}
    encoded = _encode_channel_metadata(merged)
    if encoded != channel.channel_metadata:
        channel.channel_metadata = encoded
        changed = True

    if fill_youtube_channel_id:
        profile_channel_id = profile.get("youtube_channel_id")
        if profile_channel_id and not channel.youtube_channel_id:
            channel.youtube_channel_id = profile_channel_id
            changed = True

    if fill_channel_url:
        profile_channel_url = profile.get("channel_url")
        if profile_channel_url and not channel.channel_url:
            channel.channel_url = profile_channel_url
            changed = True

    if fill_bio:
        profile_description = profile.get("description")
        if profile_description and not channel.bio:
            channel.bio = profile_description
            changed = True

    if changed:
        channel.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.add(channel)
        session.flush()
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
