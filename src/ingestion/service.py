import logging
import re
import os
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from urllib.parse import urlparse

import yt_dlp
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)
from slugify import slugify
from sqlmodel import Session, select

from src.channels import service as channels_service
from src.channels.schemas import ChannelCreate
from src.channels.service import decode_subtopics
from src.classification import service as classification_service
from src.classification.constants import TOPIC_KEYWORDS
from src.classification.models import TopicMention
from src.classification.schemas import ClassificationRequest, TopicMentionCreate
from src.ingestion.schemas import (
    IngestionActionResponse,
    IngestionClassificationInput,
    IngestionSummaryInput,
    IngestionTranscriptInput,
    IngestionYoutubeChannelRunRequest,
    IngestionYoutubeChannelRunResponse,
    IngestionYoutubeChannelRunVideoResult,
    IngestionYoutubeRequest,
    IngestionYoutubeResponse,
)
from src.config import settings
from src.llm import service as llm_service
from src.topics import service as topics_service
from src.videos import service as videos_service
from src.videos.exceptions import InvalidYouTubeUrl, TranscriptAlreadyExists
from src.videos.schemas import TranscriptCreate, VideoCreate, VideoSummaryCreate

_CHANNEL_ID_PATTERN = re.compile(r"UC[A-Za-z0-9_-]{22}")
_CHANNEL_ID_FROM_TEXT_PATTERN = re.compile(r"(UC[A-Za-z0-9_-]{22})")
_DEFAULT_SUMMARY_CHARS = 320
_DEFAULT_LONG_SUMMARY_CHARS = 1200
_DEFAULT_HIGHLIGHT_WORDS = 20
_SUBTOPIC_TO_TOPIC_SLUG = {
    "usd_try": "dolar",
    "eur_try": "dolar",
    "gold": "altin",
    "silver": "gumus",
    "bist": "borsa-istanbul",
    "deposit_rates": "faiz",
    "interest_rates": "faiz",
    "cbt_policy": "faiz",
    "inflation": "enflasyon",
    "global_markets": "dow-jones",
    "dow_jones": "dow-jones",
    "nasdaq": "dow-jones",
    "oil": "petrol",
    "crypto": "bitcoin-kripto",
    "foreign_policy": "dis-siyaset",
    "domestic_politics": "ic-siyaset",
    "geopolitics": "jeopolitik",
    "security": "jeopolitik",
    "government": "ic-siyaset",
    "opposition": "ic-siyaset",
    "elections": "ic-siyaset",
    "public_policy": "ic-siyaset",
    "leadership_statements": "ic-siyaset",
}
_MAIN_TOPIC_TO_TOPIC_SLUG = {
    "economy_finance": "ekonomi",
    "business": "ekonomi",
    "politics": "siyaset",
    "international_relations": "dis-siyaset",
    "sports": "spor",
    "technology": "teknoloji",
    "mixed": "ekonomi",
    "other": "ekonomi",
}
_STANCE_TO_SENTIMENT = {
    "positive": "bullish",
    "negative": "bearish",
    "cautious": "neutral",
    "neutral": "neutral",
    "mixed": "neutral",
}


def _llm_generation_enabled() -> bool:
    if not settings.GEMINI_API_KEY:
        return False
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return True


@dataclass
class _ChannelVideoCandidate:
    video_id: str
    video_url: str
    title: str | None
    published_at: datetime | None


@dataclass
class _ChannelPlaylistInfo:
    candidates: list["_ChannelVideoCandidate"]
    channel_name: str | None
    channel_handle: str | None


def _compact_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def _prepare_transcript_for_llm(raw_text: str, max_chars: int = 12000) -> str:
    compact = _compact_whitespace(raw_text)
    if len(compact) <= max_chars:
        return compact

    head = compact[:5000].strip()
    middle_start = max(0, len(compact) // 2 - 1000)
    middle = compact[middle_start : middle_start + 2000].strip()
    tail = compact[-5000:].strip()
    return "\n...\n".join([part for part in [head, middle, tail] if part])


def _auto_summary_from_transcript(raw_text: str, language: str = "tr") -> IngestionSummaryInput:
    compact = _compact_whitespace(raw_text)
    if not compact:
        compact = "Videonun transkript içeriği boş veya çok kısa."

    short_summary = compact[:_DEFAULT_SUMMARY_CHARS].strip()
    long_summary = compact[:_DEFAULT_LONG_SUMMARY_CHARS].strip()

    words = compact.split()
    highlights: list[str] = []
    if words:
        first = " ".join(words[:_DEFAULT_HIGHLIGHT_WORDS]).strip()
        if first:
            highlights.append(first)
        middle_start = max(0, len(words) // 2 - (_DEFAULT_HIGHLIGHT_WORDS // 2))
        middle = " ".join(words[middle_start : middle_start + _DEFAULT_HIGHLIGHT_WORDS]).strip()
        if middle and middle not in highlights:
            highlights.append(middle)
        tail = " ".join(words[-_DEFAULT_HIGHLIGHT_WORDS:]).strip()
        if tail and tail not in highlights:
            highlights.append(tail)

    return IngestionSummaryInput(
        short_summary=short_summary or "Video içeriği özetlenemedi.",
        long_summary=long_summary or None,
        highlights=highlights or None,
        language=language or "tr",
        source="manual",
    )


def _safe_str(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


def _safe_float(value: object, default: float = 0.5) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _clip_text(value: str, limit: int = 500) -> str:
    return value[:limit].strip()


def _detect_sentiment(raw_text: str) -> str:
    text = raw_text.lower()
    bearish_keywords = [
        "kriz",
        "risk",
        "düş",
        "daralma",
        "savaş",
        "baskı",
        "kayıp",
        "enflasyon yüksel",
    ]
    bullish_keywords = [
        "yüksel",
        "artış",
        "toparlan",
        "güçlü",
        "fırsat",
        "pozitif",
        "iyileş",
    ]
    bearish_score = sum(text.count(token) for token in bearish_keywords)
    bullish_score = sum(text.count(token) for token in bullish_keywords)
    if bearish_score > bullish_score:
        return "bearish"
    if bullish_score > bearish_score:
        return "bullish"
    return "neutral"


def _auto_classification_from_transcript(
    session: Session,
    raw_text: str,
) -> IngestionClassificationInput:
    normalized = raw_text.lower()
    topic_scores: list[tuple[int, str, str, int]] = []
    for topic_slug, keywords in TOPIC_KEYWORDS.items():
        topic = topics_service.get_by_slug(session, topic_slug)
        if topic is None:
            continue

        score = 0
        for token in keywords:
            token_norm = token.lower()
            score += normalized.count(token_norm)
        if score > 0:
            topic_scores.append((topic.id, topic.slug, topic.name, score))

    topic_scores.sort(key=lambda item: item[3], reverse=True)
    selected = topic_scores[:3]

    if not selected:
        fallback_topic = topics_service.get_by_slug(session, "ekonomi")
        if fallback_topic is None:
            all_topics = topics_service.list_all(session)
            if not all_topics:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="No topics available for auto-classification.",
                )
            fallback_topic = all_topics[0]
        selected = [(fallback_topic.id, fallback_topic.slug, fallback_topic.name, 1)]

    sentiment = _detect_sentiment(raw_text)
    compact = _compact_whitespace(raw_text)
    mention_summary = compact[:500].strip() or "Video içeriğinde bu konuya dair görüş paylaşıldı."

    mentions: list[TopicMentionCreate] = []
    for topic_id, _topic_slug, topic_name, score in selected:
        confidence = min(0.95, 0.55 + (score * 0.08))
        mentions.append(
            TopicMentionCreate(
                topic_id=topic_id,
                summary=f"{topic_name}: {mention_summary}",
                sentiment=sentiment,
                key_levels=None,
                start_time=None,
                end_time=None,
                confidence=round(confidence, 2),
            )
        )

    return IngestionClassificationInput(
        source="manual",
        topic_mentions=mentions,
    )


def _build_llm_metadata(
    *,
    data: IngestionYoutubeRequest,
    channel,
    video,
    video_metadata: dict | None,
) -> dict[str, str]:
    channel_name = _safe_str(getattr(channel, "name", None))
    if not channel_name:
        channel_name = _safe_str(video_metadata.get("author_name") if video_metadata else "")

    video_title = _safe_str(getattr(video, "title", None))
    if not video_title:
        video_title = _safe_str(data.video.title or "")
    if not video_title and video_metadata:
        video_title = _safe_str(video_metadata.get("title"))

    published_at = ""
    raw_published = getattr(video, "published_at", None)
    if raw_published is not None:
        published_at = raw_published.isoformat()

    # Channel-level topic context for the LLM prompt.
    channel_primary_topic = _safe_str(getattr(channel, "primary_topic_slug", None))
    subtopics_list = decode_subtopics(getattr(channel, "expected_subtopics", None))
    channel_expected_subtopics = ", ".join(subtopics_list) if subtopics_list else ""

    return {
        "source_platform": "youtube",
        "channel_name": channel_name or "unknown",
        "speaker_name": channel_name or "unknown",
        "video_title": video_title or "unknown",
        "published_at": published_at or "unknown",
        "source_url": videos_service.canonicalize_youtube_url(data.video.video_url),
        "channel_primary_topic": channel_primary_topic,
        "channel_expected_subtopics": channel_expected_subtopics,
    }


def _summary_from_llm_payload(
    payload: dict,
    *,
    transcript_language: str,
) -> IngestionSummaryInput | None:
    summary_block = payload.get("summary")
    if not isinstance(summary_block, dict):
        return None

    short_summary = _safe_str(summary_block.get("short"))
    detailed_summary = _safe_str(summary_block.get("detailed"))
    if not short_summary and detailed_summary:
        short_summary = _clip_text(detailed_summary, _DEFAULT_SUMMARY_CHARS)
    if not short_summary:
        return None

    highlights: list[str] = []
    key_points = payload.get("key_points")
    if isinstance(key_points, list):
        for item in key_points[:5]:
            if isinstance(item, dict):
                point = _safe_str(item.get("point"))
                if point:
                    highlights.append(point)
    if not highlights:
        takeaways = payload.get("actionable_takeaways")
        if isinstance(takeaways, list):
            for item in takeaways[:5]:
                if isinstance(item, dict):
                    takeaway = _safe_str(item.get("takeaway"))
                    if takeaway:
                        highlights.append(takeaway)

    language = _safe_str(payload.get("language"), transcript_language) or transcript_language
    return IngestionSummaryInput(
        short_summary=short_summary,
        long_summary=detailed_summary or None,
        highlights=highlights or None,
        language=language,
        source="llm",
    )


def _resolve_topic_slug_from_llm(
    *,
    subtopic: str,
    topic: str,
    primary_topic: str,
) -> str | None:
    normalized_subtopic = subtopic.strip().lower()

    # Handle "other:<slug>" format — novel topics from the LLM.
    # Return None so the caller can decide whether to create or skip.
    if normalized_subtopic.startswith("other:"):
        return None

    if normalized_subtopic in _SUBTOPIC_TO_TOPIC_SLUG:
        return _SUBTOPIC_TO_TOPIC_SLUG[normalized_subtopic]

    normalized_topic = topic.strip().lower()
    if normalized_topic in _SUBTOPIC_TO_TOPIC_SLUG:
        return _SUBTOPIC_TO_TOPIC_SLUG[normalized_topic]
    if normalized_topic in _MAIN_TOPIC_TO_TOPIC_SLUG:
        return _MAIN_TOPIC_TO_TOPIC_SLUG[normalized_topic]

    normalized_primary = primary_topic.strip().lower()
    return _MAIN_TOPIC_TO_TOPIC_SLUG.get(normalized_primary, "ekonomi")


def _classification_from_llm_payload(
    session: Session,
    payload: dict,
) -> IngestionClassificationInput | None:
    primary_topic = ""
    primary_topic_block = payload.get("primary_topic")
    if isinstance(primary_topic_block, dict):
        primary_topic = _safe_str(primary_topic_block.get("label"))

    grouped: dict[int, dict[str, object]] = {}

    def push_record(
        *,
        subtopic: str,
        topic: str,
        summary: str,
        evidence: str,
        stance: str,
        confidence: float,
    ) -> None:
        topic_slug = _resolve_topic_slug_from_llm(
            subtopic=subtopic,
            topic=topic,
            primary_topic=primary_topic,
        )
        if topic_slug is None:
            # "other:*" novel topic — skip, no matching DB topic.
            logger.info("Skipping novel subtopic '%s' (no DB topic)", subtopic)
            return
        topic_model = topics_service.get_by_slug(session, topic_slug)
        if topic_model is None:
            return

        topic_payload = grouped.get(topic_model.id)
        if topic_payload is None:
            topic_payload = {
                "topic_name": topic_model.name,
                "summaries": [],
                "stances": [],
                "confidences": [],
            }
            grouped[topic_model.id] = topic_payload

        content = _safe_str(summary)
        if not content:
            content = _safe_str(evidence)
        if content:
            summaries = topic_payload["summaries"]
            if isinstance(summaries, list):
                summaries.append(content)

        stances = topic_payload["stances"]
        if isinstance(stances, list):
            stances.append(_safe_str(stance).lower() or "neutral")

        confidences = topic_payload["confidences"]
        if isinstance(confidences, list):
            confidences.append(confidence)

    topic_segments = payload.get("topic_segments")
    if isinstance(topic_segments, list):
        for item in topic_segments:
            if not isinstance(item, dict):
                continue
            push_record(
                subtopic=_safe_str(item.get("subtopic")),
                topic=_safe_str(item.get("topic")),
                summary=_safe_str(item.get("summary")),
                evidence=_safe_str(item.get("evidence")),
                stance=_safe_str(item.get("stance")),
                confidence=_safe_float(item.get("confidence"), 0.6),
            )

    timeline_records = payload.get("timeline_records")
    if isinstance(timeline_records, list):
        for item in timeline_records:
            if not isinstance(item, dict):
                continue
            push_record(
                subtopic=_safe_str(item.get("subtopic")),
                topic=_safe_str(item.get("topic")),
                summary=_safe_str(item.get("what_was_said")),
                evidence=_safe_str(item.get("evidence")),
                stance=_safe_str(item.get("stance")),
                confidence=_safe_float(item.get("confidence"), 0.6),
            )

    if not grouped:
        return None

    mentions: list[TopicMentionCreate] = []
    for topic_id, values in grouped.items():
        topic_name = str(values.get("topic_name") or "Konu")
        summaries = values.get("summaries")
        stances = values.get("stances")
        confidences = values.get("confidences")
        if not isinstance(summaries, list) or not summaries:
            mention_text = "Video içeriğinde bu konuya dair değerlendirme yapıldı."
        else:
            mention_text = _clip_text(_safe_str(summaries[0]), 500)

        dominant_stance = "neutral"
        if isinstance(stances, list) and stances:
            dominant_stance = _safe_str(stances[0], "neutral")
        sentiment = _STANCE_TO_SENTIMENT.get(dominant_stance, "neutral")

        avg_confidence = 0.6
        if isinstance(confidences, list) and confidences:
            numeric = [float(x) for x in confidences if isinstance(x, (int, float))]
            if numeric:
                avg_confidence = _safe_float(mean(numeric), 0.6)

        mentions.append(
            TopicMentionCreate(
                topic_id=topic_id,
                summary=f"{topic_name}: {mention_text}",
                sentiment=sentiment,
                key_levels=None,
                start_time=None,
                end_time=None,
                confidence=round(avg_confidence, 2),
            )
        )

    return IngestionClassificationInput(source="llm", topic_mentions=mentions[:5])


def _auto_fill_missing_analytics_for_new_video(
    session: Session,
    data: IngestionYoutubeRequest,
    *,
    transcript_text: str,
    transcript_language: str,
    channel,
    video,
    video_metadata: dict | None,
) -> None:
    # Fill summary/classification whenever they are absent from both the request and the DB.
    # This runs for new and reused videos alike, so re-ingesting a video with missing analytics
    # will always fill them in without duplicating existing data.
    needs_summary = data.summary is None and videos_service.get_summary(session, video.id) is None
    has_classification = session.exec(
        select(TopicMention).where(TopicMention.video_id == video.id)
    ).first() is not None
    needs_classification = data.classification is None and not has_classification
    if not needs_summary and not needs_classification:
        return

    llm_meta = _build_llm_metadata(
        data=data,
        channel=channel,
        video=video,
        video_metadata=video_metadata,
    )
    llm_transcript = _prepare_transcript_for_llm(transcript_text)

    # Single merged LLM call for both summary and classification.
    llm_payload: dict | None = None
    if _llm_generation_enabled() and (needs_summary or needs_classification):
        try:
            llm_payload = llm_service.generate_analysis_json(
                source_platform=llm_meta["source_platform"],
                channel_name=llm_meta["channel_name"],
                speaker_name=llm_meta["speaker_name"],
                video_title=llm_meta["video_title"],
                published_at=llm_meta["published_at"],
                source_url=llm_meta["source_url"],
                transcript=llm_transcript,
                channel_primary_topic=llm_meta["channel_primary_topic"],
                channel_expected_subtopics=llm_meta["channel_expected_subtopics"],
            )
            logger.info("LLM analysis generated for video_url=%s", llm_meta["source_url"])
        except llm_service.LLMGenerationError:
            logger.warning(
                "LLM analysis failed for video_url=%s, falling back to auto-generation",
                llm_meta["source_url"],
                exc_info=True,
            )
            llm_payload = None

    if needs_summary:
        llm_summary: IngestionSummaryInput | None = None
        if llm_payload is not None:
            llm_summary = _summary_from_llm_payload(
                llm_payload,
                transcript_language=transcript_language,
            )
        data.summary = llm_summary or _auto_summary_from_transcript(
            transcript_text,
            transcript_language,
        )

    if needs_classification:
        llm_classification: IngestionClassificationInput | None = None
        if llm_payload is not None:
            llm_classification = _classification_from_llm_payload(session, llm_payload)
        data.classification = llm_classification or _auto_classification_from_transcript(
            session,
            transcript_text,
        )


def _resolve_channel(
    session: Session,
    data: IngestionYoutubeRequest,
    video_metadata: dict | None,
):
    channel_name: str | None = None
    channel_handle: str | None = None
    channel_url: str | None = None
    channel_bio = data.person.bio if data.person else None

    if video_metadata:
        metadata_name = str(video_metadata.get("author_name", "")).strip()
        metadata_author_url = str(video_metadata.get("author_url", "")).strip()
        metadata_handle = videos_service.extract_youtube_channel_handle(metadata_author_url)

        if metadata_name:
            channel_name = metadata_name
        if metadata_handle:
            channel_handle = metadata_handle
        if metadata_author_url:
            channel_url = metadata_author_url

    if data.person:
        if not channel_name and data.person.name:
            channel_name = data.person.name.strip()
        if not channel_handle and data.person.platform_handle:
            channel_handle = data.person.platform_handle.strip()

    if channel_handle:
        by_handle = channels_service.get_by_channel_handle(session, channel_handle)
        if by_handle:
            updated = False
            if by_handle.legacy_person_id is not None:
                by_handle.legacy_person_id = None
                updated = True
            if channel_url and not by_handle.channel_url:
                by_handle.channel_url = channel_url
                updated = True
            if channel_bio and not by_handle.bio:
                by_handle.bio = channel_bio
                updated = True
            if updated:
                session.add(by_handle)
                session.flush()
                session.refresh(by_handle)
            return by_handle, "reused"

    if channel_name:
        by_slug = channels_service.get_by_slug(session, slugify(channel_name))
        if by_slug:
            updated = False
            if by_slug.legacy_person_id is not None:
                by_slug.legacy_person_id = None
                updated = True
            if channel_handle and not by_slug.channel_handle:
                by_slug.channel_handle = channel_handle
                updated = True
            if channel_url and not by_slug.channel_url:
                by_slug.channel_url = channel_url
                updated = True
            if channel_bio and not by_slug.bio:
                by_slug.bio = channel_bio
                updated = True
            if updated:
                session.add(by_slug)
                session.flush()
                session.refresh(by_slug)
            return by_slug, "reused"

    if not channel_name and channel_handle:
        channel_name = channel_handle.lstrip("@")

    if not channel_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Could not resolve YouTube channel owner from metadata. "
                "Provide person.name or person.platform_handle in payload."
            ),
        )

    created = channels_service.create(
        session,
        ChannelCreate(
            name=channel_name,
            platform="youtube",
            channel_handle=channel_handle,
            channel_url=channel_url,
            bio=channel_bio,
            legacy_person_id=None,
        ),
    )
    return created, "created"


def _resolve_published_at(
    data: IngestionYoutubeRequest,
    video_metadata: dict | None = None,
) -> datetime | None:
    """Return published_at from payload, then metadata, then give up."""
    if data.video.published_at is not None:
        return data.video.published_at
    if video_metadata:
        publish_date = video_metadata.get("publish_date")
        if isinstance(publish_date, datetime):
            return publish_date
    return None


def _resolve_video(
    session: Session,
    data: IngestionYoutubeRequest,
    channel_id: int,
    person_id: int | None,
    video_metadata: dict | None,
):
    if not videos_service.extract_youtube_id(data.video.video_url):
        raise InvalidYouTubeUrl()

    existing = videos_service.get_by_url(session, data.video.video_url)
    if existing:
        # Keep video linked to the detected owner channel when duplicates are found.
        if existing.channel_id != channel_id:
            existing.channel_id = channel_id
        if person_id is not None and existing.person_id != person_id:
            existing.person_id = person_id
        if person_id is None and existing.person_id is not None:
            existing.person_id = None

        if not existing.title and video_metadata and isinstance(video_metadata.get("title"), str):
            existing.title = str(video_metadata["title"])
        elif not existing.title and data.video.title:
            existing.title = data.video.title

        if existing.published_at is None:
            existing.published_at = _resolve_published_at(data, video_metadata)

        session.add(existing)
        session.flush()
        session.refresh(existing)
        return existing, "reused"

    title = data.video.title
    if not title and video_metadata and isinstance(video_metadata.get("title"), str):
        title = str(video_metadata["title"])

    published_at = _resolve_published_at(data, video_metadata)

    duration = data.video.duration
    if duration is None and video_metadata:
        raw_duration = video_metadata.get("duration")
        if isinstance(raw_duration, (int, float)):
            duration = int(raw_duration)

    created = videos_service.create(
        session,
        VideoCreate(
            channel_id=channel_id,
            person_id=person_id,
            video_url=data.video.video_url,
            title=title,
            published_at=published_at,
            duration=duration,
        ),
    )
    return created, "created"


def _resolve_transcript_input(
    session: Session,
    data: IngestionYoutubeRequest,
    video,
) -> IngestionTranscriptInput:
    """Return transcript from request, existing DB record, or auto-fetch from YouTube."""
    if data.transcript is not None:
        return data.transcript

    # Reuse existing transcript when not overwriting — avoid unnecessary YouTube fetch.
    if not data.overwrite.transcript:
        existing = videos_service.get_transcript(session, video.id)
        if existing:
            return IngestionTranscriptInput(
                raw_text=existing.raw_text,
                language=existing.language,
            )

    video_id = videos_service.extract_youtube_id(data.video.video_url)
    if not video_id:
        raise InvalidYouTubeUrl()

    try:
        fetched = videos_service.fetch_transcript_from_youtube(
            video_id, data.transcript_languages
        )
    except videos_service.YouTubeTranscriptFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Could not fetch transcript from YouTube: {exc.detail}",
        ) from exc

    return IngestionTranscriptInput(
        raw_text=fetched["full_text"],
        language=str(fetched.get("language", "tr")),
    )


def _apply_transcript(session: Session, transcript_input: IngestionTranscriptInput, video, *, overwrite: bool):
    transcript = videos_service.get_transcript(session, video.id)

    if transcript:
        if not overwrite:
            # Transcript already exists and we're not overwriting — nothing to do.
            return transcript, "skipped"
        updated = videos_service.update_transcript(
            session,
            transcript,
            raw_text=transcript_input.raw_text,
            language=transcript_input.language,
        )
        return updated, "updated"

    created = videos_service.add_transcript(
        session,
        video,
        TranscriptCreate(
            raw_text=transcript_input.raw_text,
            language=transcript_input.language,
        ),
    )
    return created, "created"


def _apply_summary(session: Session, data: IngestionYoutubeRequest, video):
    if data.summary is None:
        return None, "skipped"

    summary_payload = VideoSummaryCreate(
        short_summary=data.summary.short_summary,
        long_summary=data.summary.long_summary,
        highlights=data.summary.highlights,
        language=data.summary.language,
        source=data.summary.source,
    )
    existing = videos_service.get_summary(session, video.id)
    if existing:
        if not data.overwrite.summary:
            return existing, "skipped"
        updated = videos_service.update_summary(session, existing, summary_payload)
        return updated, "updated"

    created = videos_service.add_summary(session, video, summary_payload)
    return created, "created"


def _validate_topic_ids(session: Session, data: IngestionYoutubeRequest) -> None:
    if data.classification is None:
        return

    missing_topic_ids = []
    for mention in data.classification.topic_mentions:
        if topics_service.get_by_id(session, mention.topic_id) is None:
            missing_topic_ids.append(mention.topic_id)

    if missing_topic_ids:
        unique_ids = sorted(set(missing_topic_ids))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid topic_id values: {unique_ids}",
        )


def _apply_classification(session: Session, data: IngestionYoutubeRequest, video):
    if data.classification is None:
        return 0, "skipped"

    existing_mentions = list(
        session.exec(select(TopicMention).where(TopicMention.video_id == video.id)).all()
    )
    if existing_mentions and not data.overwrite.classification:
        return len(existing_mentions), "skipped"

    classification_result = classification_service.classify_video(
        session,
        video,
        ClassificationRequest(topic_mentions=data.classification.topic_mentions),
    )
    if existing_mentions:
        return classification_result.total_mentions, "replaced"
    return classification_result.total_mentions, "created"


def _extract_channel_id_from_input(youtube_channel: str) -> str | None:
    value = youtube_channel.strip()
    if _CHANNEL_ID_PATTERN.fullmatch(value):
        return value

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "channel":
            candidate = parts[1]
            if _CHANNEL_ID_PATTERN.fullmatch(candidate):
                return candidate

        for part in parts:
            if _CHANNEL_ID_PATTERN.fullmatch(part):
                return part

    match = _CHANNEL_ID_FROM_TEXT_PATTERN.search(value)
    if match:
        return match.group(1)
    return None


def _extract_channel_handle_from_input(youtube_channel: str) -> str | None:
    value = youtube_channel.strip()
    if value.startswith("@"):
        return value

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        parts = [part for part in parsed.path.split("/") if part]
        for part in parts:
            if part.startswith("@"):
                return part

    if value and "/" not in value and " " not in value and "." not in value:
        return f"@{value}"
    return None


def _fetch_channel_id_from_handle(channel_handle: str) -> str:
    normalized = channel_handle if channel_handle.startswith("@") else f"@{channel_handle}"
    channel_url = f"https://www.youtube.com/{normalized}"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": 1,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not resolve a valid YouTube channel ID from the provided channel value.",
        ) from exc

    channel_id = (info or {}).get("channel_id") or (info or {}).get("id") or ""
    if not _CHANNEL_ID_PATTERN.fullmatch(channel_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not resolve a valid YouTube channel ID from the provided channel value.",
        )
    return channel_id


def _resolve_youtube_channel_id(youtube_channel: str) -> str:
    channel_id = _extract_channel_id_from_input(youtube_channel)
    if channel_id:
        return channel_id

    channel_handle = _extract_channel_handle_from_input(youtube_channel)
    if channel_handle:
        return _fetch_channel_id_from_handle(channel_handle)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Provide a valid YouTube channel ID, handle, or URL.",
    )


def _list_recent_channel_videos(channel_id: str, limit: int) -> _ChannelPlaylistInfo:
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": limit,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(channel_url, download=False)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch YouTube channel videos.",
        ) from exc

    info = info or {}
    # Channel-level metadata from the playlist response
    channel_name: str | None = str(info.get("channel") or info.get("uploader") or "").strip() or None
    raw_handle = str(info.get("uploader_id") or "").strip()
    channel_handle: str | None = raw_handle if raw_handle else None

    entries = info.get("entries") or []
    candidates: list[_ChannelVideoCandidate] = []
    for entry in entries[:limit]:
        if not entry:
            continue
        video_id = (entry.get("id") or "").strip()
        if not video_id:
            continue

        title = entry.get("title") or None

        published_at: datetime | None = None
        upload_date = entry.get("upload_date")
        if upload_date:
            try:
                published_at = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=None)
            except ValueError:
                pass

        candidates.append(
            _ChannelVideoCandidate(
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                title=title,
                published_at=published_at,
            )
        )
    return _ChannelPlaylistInfo(
        candidates=candidates,
        channel_name=channel_name,
        channel_handle=channel_handle,
    )


def ingest_youtube(
    session: Session,
    data: IngestionYoutubeRequest,
) -> IngestionYoutubeResponse:
    try:
        return _ingest_youtube_pipeline(session, data)
    except Exception as exc:
        session.rollback()
        logger.error(
            "Ingestion pipeline failed for video_url=%s: %s",
            getattr(data.video, "video_url", "unknown"),
            exc,
            exc_info=True,
        )
        raise


def ingest_youtube_by_url(
    session: Session,
    video_url: str,
    transcript_languages: list[str] | None = None,
) -> IngestionYoutubeResponse:
    """Convenience wrapper: ingest a single YouTube video given only its URL."""
    data = IngestionYoutubeRequest(
        video={"video_url": video_url},
        transcript_languages=transcript_languages,
    )
    return ingest_youtube(session, data)


def _ingest_youtube_pipeline(
    session: Session,
    data: IngestionYoutubeRequest,
) -> IngestionYoutubeResponse:
    video_metadata = None
    try:
        video_metadata = videos_service.fetch_youtube_metadata(data.video.video_url)
    except videos_service.YouTubeMetadataFetchError:
        logger.warning(
            "YouTube metadata fetch failed for url=%s, using payload data",
            data.video.video_url,
            exc_info=True,
        )
        video_metadata = None

    channel, channel_action = _resolve_channel(
        session,
        data,
        video_metadata,
    )
    person_action = "skipped"

    video, video_action = _resolve_video(
        session,
        data,
        channel.id,
        None,
        video_metadata,
    )

    transcript_input = _resolve_transcript_input(session, data, video)
    transcript, transcript_action = _apply_transcript(
        session, transcript_input, video, overwrite=data.overwrite.transcript
    )
    _auto_fill_missing_analytics_for_new_video(
        session,
        data,
        transcript_text=transcript.raw_text,
        transcript_language=transcript.language,
        channel=channel,
        video=video,
        video_metadata=video_metadata,
    )
    _validate_topic_ids(session, data)

    summary, summary_action = _apply_summary(session, data, video)

    classification_mentions, classification_action = _apply_classification(session, data, video)

    # Commit all flushed changes from the pipeline as one transaction.
    session.commit()

    actions = IngestionActionResponse(
        person=person_action,
        channel=channel_action,
        video=video_action,
        transcript=transcript_action,
        summary=summary_action,
        classification=classification_action,
    )

    return IngestionYoutubeResponse(
        status="stored",
        person_id=None,
        channel_id=channel.id,
        video_id=video.id,
        transcript_id=transcript.id,
        summary_id=summary.id if summary else None,
        classification_mentions=classification_mentions,
        actions=actions,
    )


def ingest_youtube_channel(
    session: Session,
    data: IngestionYoutubeChannelRunRequest,
) -> IngestionYoutubeChannelRunResponse:
    channel_id = _resolve_youtube_channel_id(data.youtube_channel)
    playlist_info = _list_recent_channel_videos(channel_id, data.video_count)
    candidates = playlist_info.candidates

    # Build a person hint from what we already know about the channel so that
    # _resolve_channel can identify the owner even when per-video metadata fetch
    # fails (e.g. YouTube bot-detection blocks yt-dlp on the server).
    person_hint: dict | None = None
    if playlist_info.channel_name or playlist_info.channel_handle:
        person_hint = {
            "name": playlist_info.channel_name,
            "platform_handle": playlist_info.channel_handle,
        }

    videos_ingested = 0
    videos_skipped_existing = 0
    videos_skipped_no_transcript = 0
    errors_count = 0
    results: list[IngestionYoutubeChannelRunVideoResult] = []

    for candidate in candidates:
        existing_video = videos_service.get_by_video_id(session, candidate.video_id)
        if existing_video is None:
            existing_video = videos_service.get_by_url(session, candidate.video_url)

        if existing_video is not None:
            videos_skipped_existing += 1
            results.append(
                IngestionYoutubeChannelRunVideoResult(
                    youtube_video_id=candidate.video_id,
                    video_url=candidate.video_url,
                    status="skipped_existing",
                    video_id=existing_video.id,
                    detail="Video is already extracted in database.",
                )
            )
            continue

        try:
            fetched_transcript = videos_service.fetch_transcript_from_youtube(
                candidate.video_id,
                data.transcript_languages,
            )
        except videos_service.YouTubeTranscriptFetchError as exc:
            videos_skipped_no_transcript += 1
            errors_count += 1
            results.append(
                IngestionYoutubeChannelRunVideoResult(
                    youtube_video_id=candidate.video_id,
                    video_url=candidate.video_url,
                    status="skipped_transcript_unavailable",
                    detail=exc.detail,
                )
            )
            continue

        ingestion_payload = IngestionYoutubeRequest(
            person=person_hint,
            video={
                "video_url": candidate.video_url,
                "title": candidate.title,
                "published_at": candidate.published_at,
            },
            transcript={
                "raw_text": fetched_transcript["full_text"],
                "language": str(fetched_transcript.get("language", "tr")),
            },
            overwrite={"transcript": True, "summary": True, "classification": True},
        )

        try:
            ingestion_result = ingest_youtube(session, ingestion_payload)
        except HTTPException as exc:
            logger.warning(
                "Ingestion HTTP error for video_id=%s: %s",
                candidate.video_id,
                exc.detail,
            )
            errors_count += 1
            results.append(
                IngestionYoutubeChannelRunVideoResult(
                    youtube_video_id=candidate.video_id,
                    video_url=candidate.video_url,
                    status="failed",
                    detail=str(exc.detail),
                )
            )
            continue
        except (ValueError, TypeError, KeyError) as exc:
            logger.error(
                "Ingestion failed for video_id=%s: %s",
                candidate.video_id,
                exc,
                exc_info=True,
            )
            errors_count += 1
            results.append(
                IngestionYoutubeChannelRunVideoResult(
                    youtube_video_id=candidate.video_id,
                    video_url=candidate.video_url,
                    status="failed",
                    detail=f"Ingestion error: {type(exc).__name__}: {exc}",
                )
            )
            continue

        videos_ingested += 1
        results.append(
            IngestionYoutubeChannelRunVideoResult(
                youtube_video_id=candidate.video_id,
                video_url=candidate.video_url,
                status="ingested",
                video_id=ingestion_result.video_id,
            )
        )

    return IngestionYoutubeChannelRunResponse(
        status="partial" if errors_count > 0 else "completed",
        youtube_channel_id=channel_id,
        requested_video_count=data.video_count,
        videos_detected=len(candidates),
        videos_ingested=videos_ingested,
        videos_skipped_existing=videos_skipped_existing,
        videos_skipped_no_transcript=videos_skipped_no_transcript,
        errors_count=errors_count,
        results=results,
    )
