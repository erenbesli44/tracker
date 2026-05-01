import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, status
from slugify import slugify
from sqlmodel import Session, select

from src.channels import service as channels_service
from src.channels.schemas import ChannelCreate
from src.classification import service as classification_service
from src.classification.constants import TOPIC_KEYWORDS
from src.classification.models import TopicMention
from src.classification.schemas import ClassificationRequest, TopicMentionCreate
from src.config import settings
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
from src.llm import service as llm_service
from src.persons import service as persons_service
from src.topics import service as topics_service
from src.videos import service as videos_service
from src.videos.exceptions import InvalidYouTubeUrl
from src.videos.schemas import TranscriptCreate, VideoCreate, VideoSummaryCreate

logger = logging.getLogger(__name__)

_CHANNEL_ID_PATTERN = re.compile(r"UC[A-Za-z0-9_-]{22}")
_CHANNEL_ID_FROM_TEXT_PATTERN = re.compile(r"(UC[A-Za-z0-9_-]{22})")
_DEFAULT_SUMMARY_CHARS = 320
_DEFAULT_LONG_SUMMARY_CHARS = 1200
_DEFAULT_HIGHLIGHT_WORDS = 20
# LLM output aliases -> canonical DB topic slugs.
# Canonical slugs are the current (broadened) taxonomy. Old slugs and common
# English variants stay as aliases so historical LLM outputs still resolve.
_SUBTOPIC_TO_TOPIC_SLUG = {
    # ── Canonical slugs (current taxonomy) ────────────────────────────────
    "bist-turk-piyasalari": "bist-turk-piyasalari",
    "altin": "altin",
    "gumus": "gumus",
    "doviz-kur": "doviz-kur",
    "kripto-paralar": "kripto-paralar",
    "amerikan-piyasalari": "amerikan-piyasalari",
    "petrol-enerji": "petrol-enerji",
    "faiz-para-politikasi": "faiz-para-politikasi",
    "enflasyon": "enflasyon",
    "ic-siyaset": "ic-siyaset",
    "jeopolitik": "jeopolitik",
    # ── Legacy Turkish slugs (pre-broadening) ─────────────────────────────
    "borsa-istanbul": "bist-turk-piyasalari",
    "dolar": "doviz-kur",
    "bitcoin-kripto": "kripto-paralar",
    "dow-jones": "amerikan-piyasalari",
    "petrol": "petrol-enerji",
    "faiz": "faiz-para-politikasi",
    "dis-siyaset": "jeopolitik",
    # ── English variants the LLM sometimes emits despite the prompt ───────
    "usd_try": "doviz-kur",
    "eur_try": "doviz-kur",
    "fx": "doviz-kur",
    "forex": "doviz-kur",
    "gold": "altin",
    "silver": "gumus",
    "bist": "bist-turk-piyasalari",
    "turkish_stocks": "bist-turk-piyasalari",
    "deposit_rates": "faiz-para-politikasi",
    "interest_rates": "faiz-para-politikasi",
    "cbt_policy": "faiz-para-politikasi",
    "monetary_policy": "faiz-para-politikasi",
    "inflation": "enflasyon",
    "global_markets": "amerikan-piyasalari",
    "us_markets": "amerikan-piyasalari",
    "wall_street": "amerikan-piyasalari",
    "dow_jones": "amerikan-piyasalari",
    "sp500": "amerikan-piyasalari",
    "nasdaq": "amerikan-piyasalari",
    "oil": "petrol-enerji",
    "brent": "petrol-enerji",
    "energy": "petrol-enerji",
    "crypto": "kripto-paralar",
    "bitcoin": "kripto-paralar",
    "btc": "kripto-paralar",
    "foreign_policy": "jeopolitik",
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
    "ekonomi": "ekonomi",
    "economy_finance": "ekonomi",
    "economy": "ekonomi",
    "business": "ekonomi",
    "siyaset": "siyaset",
    "politics": "siyaset",
    "spor": "spor",
    "international_relations": "jeopolitik",
    "sports": "spor",
    "teknoloji": "teknoloji",
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
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return bool(settings.MINIMAX_BASE_URL and settings.MINIMAX_API_KEY)


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
    profile: dict[str, Any] | None = None


def _playlist_author_url(channel_id: str, playlist_info: _ChannelPlaylistInfo) -> str:
    if playlist_info.channel_handle:
        return f"https://www.youtube.com/{playlist_info.channel_handle}"
    return f"https://www.youtube.com/channel/{channel_id}"


def _playlist_person_hint(playlist_info: _ChannelPlaylistInfo) -> dict | None:
    if not playlist_info.channel_name and not playlist_info.channel_handle:
        return None
    return {
        "name": playlist_info.channel_name or playlist_info.channel_handle.lstrip("@"),
        "platform_handle": playlist_info.channel_handle,
    }


def _playlist_video_metadata(
    channel_id: str,
    playlist_info: _ChannelPlaylistInfo,
    candidate: _ChannelVideoCandidate | None = None,
) -> dict[str, Any]:
    channel_name = playlist_info.channel_name or (
        playlist_info.channel_handle.lstrip("@") if playlist_info.channel_handle else channel_id
    )
    metadata: dict[str, Any] = {
        "author_name": channel_name,
        "author_url": _playlist_author_url(channel_id, playlist_info),
        "channel_id": channel_id,
    }
    if candidate is not None:
        metadata["title"] = candidate.title
        metadata["publish_date"] = candidate.published_at
    return metadata


def _compact_whitespace(text: str) -> str:
    return " ".join(text.split()).strip()


def _format_timecode(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _render_segments_for_llm(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for segment in segments:
        text = _compact_whitespace(_safe_str(segment.get("text")))
        if not text:
            continue
        start = max(0.0, _safe_unbounded_float(segment.get("start"), 0.0))
        duration = max(0.0, _safe_unbounded_float(segment.get("duration"), 0.0))
        end = start + duration
        lines.append(f"[{_format_timecode(start)}-{_format_timecode(end)}] {text}")
    return "\n".join(lines).strip()


def _prepare_transcript_for_llm(
    raw_text: str,
    *,
    transcript_segments: list[dict[str, Any]] | None = None,
    max_chars: int = 12000,
) -> str:
    if transcript_segments:
        rendered = _render_segments_for_llm(transcript_segments)
        if rendered:
            compact = rendered
        else:
            compact = _compact_whitespace(raw_text)
    else:
        compact = _compact_whitespace(raw_text)
    if len(compact) <= max_chars:
        return compact

    head_chars = min(5000, max(1, int(max_chars * 0.42)))
    middle_chars = min(2000, max(1, int(max_chars * 0.16)))
    tail_chars = min(5000, max(1, max_chars - head_chars - middle_chars))

    head = compact[:head_chars].strip()
    middle_start = max(0, len(compact) // 2 - (middle_chars // 2))
    middle = compact[middle_start : middle_start + middle_chars].strip()
    tail = compact[-tail_chars:].strip()
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


def _safe_unbounded_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.5) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _clip_text(value: str, limit: int = 500) -> str:
    return value[:limit].strip()


def _safe_str_list(value: object) -> list[str]:
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, (int, float)):
                text = str(item).strip()
            else:
                text = _safe_str(item)
            if text:
                result.append(text)
        return result
    if isinstance(value, str):
        text = _safe_str(value)
        return [text] if text else []
    return []


def _parse_timecode_to_seconds(value: str) -> int | None:
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        nums = [int(part) for part in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        minutes, seconds = nums
        if minutes < 0 or seconds < 0 or seconds > 59:
            return None
        return minutes * 60 + seconds
    hours, minutes, seconds = nums
    if hours < 0 or minutes < 0 or seconds < 0 or minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _normalize_expected_subtopic_slug(value: str) -> str | None:
    normalized = value.strip().lower()
    if not normalized:
        return None
    if normalized.startswith("other:"):
        return None
    return _SUBTOPIC_TO_TOPIC_SLUG.get(normalized)


def _normalize_main_topic_slug(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return "ekonomi"
    return _MAIN_TOPIC_TO_TOPIC_SLUG.get(normalized, normalized)


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
    channel_primary_topic = _normalize_main_topic_slug(
        _safe_str(getattr(channel, "primary_topic_slug", None))
    )

    return {
        "source_platform": "youtube",
        "channel_name": channel_name or "unknown",
        "speaker_name": channel_name or "unknown",
        "video_title": video_title or "unknown",
        "published_at": published_at or "unknown",
        "source_url": videos_service.canonicalize_youtube_url(data.video.video_url),
        "channel_primary_topic": channel_primary_topic,
    }


_BULLET_DICT_FALLBACK_KEYS = ("text", "summary", "bullet", "content", "value")


def _extract_bullets(raw: object, primary_key: str, limit: int = 5) -> list[str]:
    """Extract up to `limit` bullet strings from an LLM list field.

    Accepts every shape we've seen providers emit: list of plain strings,
    list of `{primary_key: "..."}` dicts, or a mix. Falls back to common
    alternate keys so future shape drift doesn't silently drop bullets.
    Warns if the field was non-empty but nothing was extracted — that's
    the exact failure mode that let highlights disappear unnoticed.
    """
    if not isinstance(raw, list):
        return []
    bullets: list[str] = []
    for item in raw[:limit]:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = _safe_str(item.get(primary_key))
            if not text:
                for alt in _BULLET_DICT_FALLBACK_KEYS:
                    text = _safe_str(item.get(alt))
                    if text:
                        break
        else:
            text = ""
        if text:
            bullets.append(text)
    if raw and not bullets:
        logger.warning(
            "LLM bullet field had %d entries but yielded 0 bullets (key=%s, sample=%r)",
            len(raw),
            primary_key,
            raw[0] if raw else None,
        )
    return bullets


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
        short_summary = detailed_summary
    if not short_summary:
        return None

    highlights = _extract_bullets(payload.get("key_points"), "point")
    if not highlights:
        highlights = _extract_bullets(payload.get("actionable_takeaways"), "takeaway")

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
    if normalized_primary in _MAIN_TOPIC_TO_TOPIC_SLUG:
        return _MAIN_TOPIC_TO_TOPIC_SLUG[normalized_primary]
    return None


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
        key_levels: list[str],
        start_time: str,
        end_time: str,
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
                "key_levels": [],
                "start_seconds": [],
                "end_seconds": [],
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

        payload_key_levels = topic_payload["key_levels"]
        if isinstance(payload_key_levels, list):
            for level in key_levels:
                level_text = _safe_str(level)
                if level_text and level_text not in payload_key_levels:
                    payload_key_levels.append(level_text)

        start_seconds = _parse_timecode_to_seconds(start_time)
        end_seconds = _parse_timecode_to_seconds(end_time)
        if start_seconds is not None:
            starts = topic_payload["start_seconds"]
            if isinstance(starts, list):
                starts.append(start_seconds)
        if end_seconds is not None:
            ends = topic_payload["end_seconds"]
            if isinstance(ends, list):
                ends.append(end_seconds)

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
                key_levels=_safe_str_list(item.get("key_levels")),
                start_time=_safe_str(item.get("start_time")),
                end_time=_safe_str(item.get("end_time")),
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
                key_levels=_safe_str_list(item.get("key_levels")),
                start_time=_safe_str(item.get("start_time")),
                end_time=_safe_str(item.get("end_time")),
                stance=_safe_str(item.get("stance")),
                confidence=_safe_float(item.get("confidence"), 0.6),
            )

    if not grouped:
        return None

    mentions: list[TopicMentionCreate] = []
    for topic_id, values in grouped.items():
        topic_name = str(values.get("topic_name") or "Konu")
        summaries = values.get("summaries")
        key_levels = values.get("key_levels")
        start_seconds = values.get("start_seconds")
        end_seconds = values.get("end_seconds")
        stances = values.get("stances")
        confidences = values.get("confidences")
        if not isinstance(summaries, list) or not summaries:
            mention_text = "Video içeriğinde bu konuya dair değerlendirme yapıldı."
        else:
            merged: list[str] = []
            for item in summaries:
                text = _safe_str(item)
                if text and text not in merged:
                    merged.append(text)
                if len(merged) >= 2:
                    break
            mention_text = " ".join(merged).strip()

        dominant_stance = "neutral"
        if isinstance(stances, list) and stances:
            dominant_stance = _safe_str(stances[0], "neutral")
        sentiment = _STANCE_TO_SENTIMENT.get(dominant_stance, "neutral")

        avg_confidence = 0.6
        if isinstance(confidences, list) and confidences:
            numeric = [float(x) for x in confidences if isinstance(x, (int, float))]
            if numeric:
                avg_confidence = _safe_float(mean(numeric), 0.6)

        start_time = None
        if isinstance(start_seconds, list):
            numeric_starts = [int(x) for x in start_seconds if isinstance(x, (int, float))]
            if numeric_starts:
                start_time = _format_timecode(float(min(numeric_starts)))

        end_time = None
        if isinstance(end_seconds, list):
            numeric_ends = [int(x) for x in end_seconds if isinstance(x, (int, float))]
            if numeric_ends:
                end_time = _format_timecode(float(max(numeric_ends)))

        mentions.append(
            TopicMentionCreate(
                topic_id=topic_id,
                summary=f"{topic_name}: {mention_text}",
                sentiment=sentiment,
                key_levels=_safe_str_list(key_levels)[:8] or None,
                start_time=start_time,
                end_time=end_time,
                confidence=round(avg_confidence, 2),
            )
        )

    return IngestionClassificationInput(source="llm", topic_mentions=mentions[:5])


def _auto_fill_missing_analytics_for_new_video(
    session: Session,
    data: IngestionYoutubeRequest,
    *,
    transcript_text: str,
    transcript_segments: list[dict[str, Any]] | None,
    transcript_language: str,
    channel,
    video,
    video_metadata: dict | None,
) -> None:
    # Fill summary/classification whenever they are absent from both the request and the DB.
    # This runs for new and reused videos alike, so re-ingesting a video with missing analytics
    # will always fill them in without duplicating existing data.
    needs_summary = data.summary is None and videos_service.get_summary(session, video.id) is None
    has_classification = (
        session.exec(select(TopicMention).where(TopicMention.video_id == video.id)).first()
        is not None
    )
    needs_classification = data.classification is None and not has_classification
    if not needs_summary and not needs_classification:
        return

    llm_meta = _build_llm_metadata(
        data=data,
        channel=channel,
        video=video,
        video_metadata=video_metadata,
    )
    llm_transcript = _prepare_transcript_for_llm(
        transcript_text,
        transcript_segments=transcript_segments,
    )

    # Single merged LLM call for both summary and classification.
    # Policy: MiniMax-only. If it fails, log and leave summary/classification
    # empty — the next scheduled run will retry, since needs_summary /
    # needs_classification become True again when the rows are still missing.
    if not _llm_generation_enabled():
        logger.error(
            "LLM is not configured; skipping analysis for video_url=%s",
            llm_meta["source_url"],
        )
        return

    try:
        llm_payload = llm_service.generate_analysis_json(
            source_platform=llm_meta["source_platform"],
            channel_name=llm_meta["channel_name"],
            speaker_name=llm_meta["speaker_name"],
            video_title=llm_meta["video_title"],
            published_at=llm_meta["published_at"],
            source_url=llm_meta["source_url"],
            transcript=llm_transcript,
            output_language=transcript_language,
            channel_primary_topic=llm_meta["channel_primary_topic"],
        )
        logger.info("LLM analysis generated for video_url=%s", llm_meta["source_url"])
    except llm_service.LLMGenerationError:
        logger.error(
            "LLM analysis failed for video_url=%s; skipping (will retry next run)",
            llm_meta["source_url"],
            exc_info=True,
        )
        return

    if needs_summary:
        data.summary = _summary_from_llm_payload(
            llm_payload,
            transcript_language=transcript_language,
        )

    if needs_classification:
        data.classification = _classification_from_llm_payload(session, llm_payload)


def _resolve_channel(
    session: Session,
    data: IngestionYoutubeRequest,
    video_metadata: dict | None,
):
    channel_name: str | None = None
    channel_handle: str | None = None
    channel_url: str | None = None
    channel_bio = data.person.bio if data.person else None
    youtube_channel_id: str | None = None

    if video_metadata:
        metadata_name = str(video_metadata.get("author_name", "")).strip()
        metadata_author_url = str(video_metadata.get("author_url", "")).strip()
        metadata_handle = videos_service.extract_youtube_channel_handle(metadata_author_url)
        metadata_channel_id = str(video_metadata.get("channel_id", "")).strip()

        if metadata_name:
            channel_name = metadata_name
        if metadata_handle:
            channel_handle = metadata_handle
        if metadata_author_url:
            channel_url = metadata_author_url
        if _CHANNEL_ID_PATTERN.fullmatch(metadata_channel_id):
            youtube_channel_id = metadata_channel_id

    if data.person:
        if not channel_name and data.person.name:
            channel_name = data.person.name.strip()
        if not channel_handle and data.person.platform_handle:
            channel_handle = data.person.platform_handle.strip()
        if data.person.id:
            person = persons_service.get_by_id(session, data.person.id)
            if person:
                if not channel_name:
                    channel_name = person.name.strip()
                if not channel_handle and person.platform_handle:
                    channel_handle = person.platform_handle.strip()
                if not channel_bio and person.bio:
                    channel_bio = person.bio

    if youtube_channel_id:
        by_youtube_id = channels_service.get_by_youtube_channel_id(session, youtube_channel_id)
        if by_youtube_id:
            updated = False
            if by_youtube_id.legacy_person_id is not None:
                by_youtube_id.legacy_person_id = None
                updated = True
            if channel_handle and not by_youtube_id.channel_handle:
                by_youtube_id.channel_handle = channel_handle
                updated = True
            if channel_url and not by_youtube_id.channel_url:
                by_youtube_id.channel_url = channel_url
                updated = True
            if channel_bio and not by_youtube_id.bio:
                by_youtube_id.bio = channel_bio
                updated = True
            if updated:
                session.add(by_youtube_id)
                session.flush()
                session.refresh(by_youtube_id)
            return by_youtube_id, "reused"

    if channel_handle:
        by_handle = channels_service.get_by_channel_handle(session, channel_handle)
        if by_handle:
            updated = False
            if by_handle.legacy_person_id is not None:
                by_handle.legacy_person_id = None
                updated = True
            if youtube_channel_id and not by_handle.youtube_channel_id:
                by_handle.youtube_channel_id = youtube_channel_id
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
            if youtube_channel_id and not by_slug.youtube_channel_id:
                by_slug.youtube_channel_id = youtube_channel_id
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
            youtube_channel_id=youtube_channel_id,
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

        if existing.duration is None:
            duration = data.video.duration
            if duration is None and video_metadata:
                raw_duration = video_metadata.get("duration")
                if isinstance(raw_duration, (int, float)):
                    duration = int(raw_duration)
            if duration is not None:
                existing.duration = duration

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
    video_metadata: dict | None,
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
                segments=videos_service.parse_transcript_segments(existing.segments_json),
            )

    video_id = videos_service.extract_youtube_id(data.video.video_url)
    if not video_id:
        raise InvalidYouTubeUrl()

    # Use the video's original language (from yt-dlp metadata) as primary preference.
    detected_lang = video_metadata.get("language") if video_metadata else None
    languages = data.transcript_languages
    if not languages and detected_lang:
        languages = [detected_lang, "en"] if detected_lang != "en" else ["en"]

    try:
        fetched = videos_service.fetch_transcript_from_youtube(video_id, languages)
    except videos_service.YouTubeTranscriptFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Could not fetch transcript from YouTube: {exc.detail}",
        ) from exc

    return IngestionTranscriptInput(
        raw_text=fetched["full_text"],
        language=str(fetched.get("language", detected_lang or "en")),
        segments=fetched.get("segments"),
    )


def _apply_transcript(
    session: Session, transcript_input: IngestionTranscriptInput, video, *, overwrite: bool
):
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
            segments=transcript_input.segments,
        )
        return updated, "updated"

    created = videos_service.add_transcript(
        session,
        video,
        TranscriptCreate(
            raw_text=transcript_input.raw_text,
            language=transcript_input.language,
            segments=transcript_input.segments,
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
        "extractor_args": {"youtube": {"lang": ["tr"]}},
        "http_headers": {"Accept-Language": "tr-TR,tr;q=0.9"},
    }
    try:
        info = videos_service.extract_info_with_yt_dlp(channel_url, opts)
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


def _resolve_youtube_channel_id_for_run(session: Session, youtube_channel: str) -> str:
    channel_id = _extract_channel_id_from_input(youtube_channel)
    if channel_id:
        return channel_id

    channel_handle = _extract_channel_handle_from_input(youtube_channel)
    if channel_handle:
        existing = channels_service.get_by_channel_handle(session, channel_handle)
        if existing and existing.youtube_channel_id:
            return existing.youtube_channel_id

    return _resolve_youtube_channel_id(youtube_channel)


def _list_recent_channel_videos(channel_id: str, limit: int) -> _ChannelPlaylistInfo:
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": limit,
        "skip_download": True,
        "extractor_args": {"youtube": {"lang": ["tr"]}},
        "http_headers": {"Accept-Language": "tr-TR,tr;q=0.9"},
    }
    try:
        info = videos_service.extract_info_with_yt_dlp(channel_url, opts)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch YouTube channel videos.",
        ) from exc

    info = info or {}
    # Channel-level metadata from the playlist response
    channel_name: str | None = (
        str(info.get("channel") or info.get("uploader") or "").strip() or None
    )
    raw_handle = str(info.get("uploader_id") or "").strip()
    channel_handle: str | None = raw_handle if raw_handle else None
    profile = videos_service.extract_channel_profile_from_info(info) if info else None

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
        profile=profile,
    )


def _ensure_channel_from_playlist(
    session: Session,
    channel_id: str,
    playlist_info: _ChannelPlaylistInfo,
) -> tuple[Any, str]:
    metadata = _playlist_video_metadata(channel_id, playlist_info)
    data = IngestionYoutubeRequest(
        person=_playlist_person_hint(playlist_info),
        video={"video_url": f"https://www.youtube.com/channel/{channel_id}"},
    )
    channel, action = _resolve_channel(session, data, metadata)
    if playlist_info.profile:
        _attach_channel_profile(
            session,
            youtube_channel_id=channel_id,
            channel_handle=playlist_info.channel_handle,
            profile=playlist_info.profile,
        )
    # Always commit so that youtube_channel_id updates from _resolve_channel are
    # persisted. Without this, channels found by handle that get youtube_channel_id
    # flushed (not committed) would lose that update if the per-video loop skips all
    # candidates — causing an unnecessary yt-dlp re-fetch on every subsequent run.
    session.commit()
    session.refresh(channel)
    return channel, action


def _ensure_video_for_transcript_attempt(
    session: Session,
    *,
    channel_id: int,
    candidate: _ChannelVideoCandidate,
    video_metadata: dict[str, Any],
):
    data = IngestionYoutubeRequest(
        video={
            "video_url": candidate.video_url,
            "title": candidate.title,
            "published_at": candidate.published_at,
        }
    )
    video, _ = _resolve_video(session, data, channel_id, None, video_metadata)
    return video


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


def _has_owner_hint(data: IngestionYoutubeRequest) -> bool:
    if data.person is None:
        return False
    return bool(data.person.id or data.person.name or data.person.platform_handle)


def _should_fetch_youtube_metadata(data: IngestionYoutubeRequest) -> bool:
    # A caller-provided transcript plus an explicit owner is already enough to
    # store the video. Avoid spending YouTube/Webshare bandwidth just to enrich
    # optional metadata; the watcher path passes playlist metadata explicitly.
    if data.transcript is not None and _has_owner_hint(data):
        return False
    return True


def _ingest_youtube_pipeline(
    session: Session,
    data: IngestionYoutubeRequest,
    *,
    video_metadata_override: dict | None = None,
) -> IngestionYoutubeResponse:
    video_metadata = video_metadata_override
    if video_metadata is None and _should_fetch_youtube_metadata(data):
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

    transcript_input = _resolve_transcript_input(session, data, video, video_metadata)
    transcript, transcript_action = _apply_transcript(
        session, transcript_input, video, overwrite=data.overwrite.transcript
    )
    _auto_fill_missing_analytics_for_new_video(
        session,
        data,
        transcript_text=transcript.raw_text,
        transcript_segments=videos_service.parse_transcript_segments(transcript.segments_json),
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
    channel_id = _resolve_youtube_channel_id_for_run(session, data.youtube_channel)
    playlist_info = _list_recent_channel_videos(channel_id, data.video_count)
    candidates = playlist_info.candidates
    channel, _channel_action = _ensure_channel_from_playlist(session, channel_id, playlist_info)

    if not playlist_info.candidates:
        logger.warning(
            "No video candidates returned for channel_id=%s (yt-dlp may be blocked or channel has no public videos)",
            channel_id,
        )

    # Build a person hint from what we already know about the channel so that
    # _resolve_channel can identify the owner even when per-video metadata fetch
    # fails (e.g. YouTube bot-detection blocks yt-dlp on the server).
    person_hint = _playlist_person_hint(playlist_info)

    videos_ingested = 0
    videos_skipped_existing = 0
    videos_skipped_no_transcript = 0
    errors_count = 0
    results: list[IngestionYoutubeChannelRunVideoResult] = []

    for candidate in candidates:
        video_metadata = _playlist_video_metadata(channel_id, playlist_info, candidate)
        existing_video = videos_service.get_by_video_id(session, candidate.video_id)
        if existing_video is None:
            existing_video = videos_service.get_by_url(session, candidate.video_url)

        if existing_video is not None:
            existing_transcript = videos_service.get_transcript(session, existing_video.id)
            if existing_transcript:
                # Backfill missing analytics for videos that were ingested before LLM
                # was configured or when LLM failed during the original ingestion run.
                needs_summary = videos_service.get_summary(session, existing_video.id) is None
                needs_classification = (
                    session.exec(
                        select(TopicMention).where(TopicMention.video_id == existing_video.id)
                    ).first()
                    is None
                )
                if needs_summary or needs_classification:
                    backfill_payload = IngestionYoutubeRequest(
                        person=person_hint,
                        video={
                            "video_url": candidate.video_url,
                            "title": candidate.title,
                            "published_at": candidate.published_at,
                        },
                        transcript={
                            "raw_text": existing_transcript.raw_text,
                            "language": existing_transcript.language,
                            "segments": videos_service.parse_transcript_segments(
                                existing_transcript.segments_json
                            ),
                        },
                        overwrite={"transcript": False, "summary": True, "classification": True},
                    )
                    try:
                        _ingest_youtube_pipeline(
                            session,
                            backfill_payload,
                            video_metadata_override=video_metadata,
                        )
                        logger.info(
                            "Analytics backfilled for video_id=%s url=%s",
                            existing_video.id,
                            candidate.video_url,
                        )
                    except Exception as exc:
                        session.rollback()
                        logger.warning(
                            "Analytics backfill failed for video_id=%s: %s",
                            existing_video.id,
                            exc,
                            exc_info=True,
                        )
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

        video = _ensure_video_for_transcript_attempt(
            session,
            channel_id=channel.id,
            candidate=candidate,
            video_metadata=video_metadata,
        )

        try:
            fetched_transcript = videos_service.fetch_transcript_from_youtube(
                candidate.video_id,
                data.transcript_languages,
            )
        except videos_service.YouTubeTranscriptFetchError as exc:
            # Only provider_error (transient infra / proxy issue) counts as an error.
            # transcript_unavailable + video_unavailable are permanent properties of the
            # video (no captions, members-only, private, age-restricted) — just skip them.
            is_hard_failure = exc.code == "provider_error"
            log_fn = logger.warning if is_hard_failure else logger.info
            log_fn(
                "Transcript skipped for video_id=%s (code=%s): %s",
                candidate.video_id,
                exc.code,
                exc.detail,
                exc_info=is_hard_failure,
            )
            videos_service.record_transcript_fetch_failure(
                session,
                video,
                code=exc.code,
                detail=exc.detail,
            )
            videos_skipped_no_transcript += 1
            if is_hard_failure:
                errors_count += 1
            results.append(
                IngestionYoutubeChannelRunVideoResult(
                    youtube_video_id=candidate.video_id,
                    video_url=candidate.video_url,
                    status="skipped_transcript_unavailable",
                    video_id=video.id,
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
                "segments": fetched_transcript.get("segments"),
            },
            overwrite={"transcript": True, "summary": True, "classification": True},
        )

        try:
            ingestion_result = _ingest_youtube_pipeline(
                session,
                ingestion_payload,
                video_metadata_override=video_metadata,
            )
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

    # After the per-video loop the channel is guaranteed to exist if any
    # video was ingested. Re-attempt profile enrichment so a newly-created
    # channel also gets avatar/banner/sub data attached.
    if playlist_info.profile:
        _attach_channel_profile(
            session,
            youtube_channel_id=channel_id,
            channel_handle=playlist_info.channel_handle,
            profile=playlist_info.profile,
        )
        session.commit()

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


def _attach_channel_profile(
    session: Session,
    *,
    youtube_channel_id: str | None,
    channel_handle: str | None,
    profile: dict[str, Any],
) -> None:
    """Look up the DB channel by youtube_channel_id / handle and merge profile data."""
    db_channel = None
    if youtube_channel_id:
        db_channel = channels_service.get_by_youtube_channel_id(session, youtube_channel_id)
    if db_channel is None and channel_handle:
        db_channel = channels_service.get_by_channel_handle(session, channel_handle)
    if db_channel is None:
        return
    channels_service.upsert_profile_metadata(session, db_channel, profile)
