"""Transcript analysis helpers — MiniMax (OpenAI-compatible) only."""

import json
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

import openai

from src.config import settings

logger = logging.getLogger(__name__)
from src.llm.prompts import (
    ANALYSIS_PROMPT_TEMPLATE,
    CLASSIFICATION_PROMPT_TEMPLATE,
    ECONOMIC_THESIS_PROMPT_TEMPLATE,
    MARKET_INFERENCE_AGENDA_RULES,
    MARKET_INFERENCE_PROMPT_TEMPLATE,
    MARKET_INFERENCE_SUMMARY_RULES,
    OUTLOOK_PROMPT_TEMPLATE,
    PARTICIPANTS_PROMPT_TEMPLATE,
    QUOTE_CLEAN_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)

_RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
_RETRYABLE_ERROR_PHRASES = (
    "high demand",
    "try again",
    "rate limit",
    "resource exhausted",
    "temporarily unavailable",
    "overloaded",
    "backend error",
    "service unavailable",
)


# Reasoning depth per call type. MiniMax-M3.1-Flash accepts low|medium|high|xhigh|max and uses
# max when the field is omitted; at max, reasoning is ~85-90 % of a call's tokens (2026-11-01).
_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
_EFFORT_SETTING_BY_PURPOSE = {
    "analysis": "MINIMAX_EFFORT_ANALYSIS",
    "outlook": "MINIMAX_EFFORT_OUTLOOK",
    "quote_clean": "MINIMAX_EFFORT_QUOTE_CLEAN",
    "participants": "MINIMAX_EFFORT_PARTICIPANTS",
    "market_inference": "MINIMAX_EFFORT_INFERENCE",
}
_USAGE_KEYS = ("calls", "prompt_tokens", "cached_tokens", "completion_tokens", "reasoning_tokens")


class LLMGenerationError(Exception):
    """Raised when structured generation cannot be completed."""


def reasoning_effort_for(purpose: str) -> str | None:
    """Configured reasoning_effort for a call purpose; None omits the field (model default)."""
    name = _EFFORT_SETTING_BY_PURPOSE.get(purpose, "MINIMAX_EFFORT_DEFAULT")
    value = str(getattr(settings, name, "") or "").strip().lower()
    if not value:
        return None
    if value not in _EFFORT_LEVELS:
        logger.warning(
            "Ignoring invalid %s=%r (allowed: %s)", name, value, ", ".join(sorted(_EFFORT_LEVELS))
        )
        return None
    return value


# In-process token totals per purpose since start (reset on restart); the youtube-watch
# pipeline health endpoint shows them. Every call also logs one "MiniMax usage" line.
_usage_lock = threading.Lock()
_usage_since = datetime.now(UTC)
_usage_totals: dict[str, dict[str, int]] = {}


def _usage_fields(completion: Any) -> dict[str, int]:
    """Token counts from the completion's usage block; a missing field counts as 0."""
    usage = getattr(completion, "usage", None)

    def _int(obj: Any, name: str) -> int:
        try:
            return int(getattr(obj, name, None) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        "calls": 1,
        "prompt_tokens": _int(usage, "prompt_tokens"),
        "cached_tokens": _int(getattr(usage, "prompt_tokens_details", None), "cached_tokens"),
        "completion_tokens": _int(usage, "completion_tokens"),
        "reasoning_tokens": _int(
            getattr(usage, "completion_tokens_details", None), "reasoning_tokens"
        ),
    }


def _record_usage(purpose: str, fields: dict[str, int]) -> None:
    with _usage_lock:
        totals = _usage_totals.setdefault(purpose, dict.fromkeys(_USAGE_KEYS, 0))
        for key in _USAGE_KEYS:
            totals[key] += fields.get(key, 0)


def usage_snapshot() -> dict[str, Any]:
    """Token totals since process start, overall and per purpose."""
    with _usage_lock:
        by_purpose = {k: dict(v) for k, v in sorted(_usage_totals.items())}
    total = dict.fromkeys(_USAGE_KEYS, 0)
    for fields in by_purpose.values():
        for key in _USAGE_KEYS:
            total[key] += fields[key]
    return {"since": _usage_since.isoformat(), "total": total, "by_purpose": by_purpose}


def _replace_placeholders(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


def _build_prompt(
    template: str,
    *,
    source_platform: str,
    channel_name: str,
    speaker_name: str,
    video_title: str,
    published_at: str,
    source_url: str,
    transcript: str,
    output_language: str = "tr",
    channel_primary_topic: str = "",
) -> str:
    values = {
        "source_platform": source_platform,
        "channel_name": channel_name,
        "speaker_name": speaker_name,
        "video_title": video_title,
        "published_at": published_at,
        "source_url": source_url,
        "transcript": transcript,
        "output_language": output_language,
        "channel_primary_topic": channel_primary_topic or "general",
    }
    return _replace_placeholders(template, values)


def _find_balanced_json_object(text: str) -> str | None:
    """Find the first balanced top-level JSON object in text using brace depth."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _extract_json_payload(raw_text: str) -> dict[str, Any]:
    candidate = raw_text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        lines = [line for line in lines if not line.strip().startswith("```")]
        candidate = "\n".join(lines).strip()

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        # Fallback: extract the first balanced JSON object when model adds wrapper text.
        json_str = _find_balanced_json_object(candidate)
        if json_str is None:
            raise LLMGenerationError("LLM output is not valid JSON.")
        try:
            parsed = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise LLMGenerationError("LLM output is not valid JSON.") from exc

    if not isinstance(parsed, dict):
        raise LLMGenerationError("LLM output root must be a JSON object.")
    return parsed


def _compact_error_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _is_retryable_http_error(status_code: int, body: str) -> bool:
    if status_code in _RETRYABLE_HTTP_CODES:
        return True
    lower_body = body.lower()
    return any(phrase in lower_body for phrase in _RETRYABLE_ERROR_PHRASES)


def _retry_delay_seconds(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, settings.MINIMAX_RETRY_MAX_DELAY_SECONDS)
    base = max(0.1, float(settings.MINIMAX_RETRY_BASE_DELAY_SECONDS))
    max_delay = max(base, float(settings.MINIMAX_RETRY_MAX_DELAY_SECONDS))
    return min(max_delay, base * (2 ** (attempt - 1)))


def _extract_text_from_openai_completion(completion: Any) -> str:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        raise LLMGenerationError("MiniMax response has no choices.")
    choice = choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise LLMGenerationError(
            "MiniMax truncated output (finish_reason=length); falling back."
        )
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not isinstance(content, str) or not content.strip():
        raise LLMGenerationError("MiniMax response has no text content.")
    return content.strip()


def _retry_after_from_openai_error(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _call_minimax_json(prompt: str, *, purpose: str = "default") -> dict[str, Any]:
    if not settings.MINIMAX_BASE_URL or not settings.MINIMAX_API_KEY:
        raise LLMGenerationError("MINIMAX_BASE_URL and MINIMAX_API_KEY must be configured.")

    model_name = settings.MINIMAX_MODEL or "MiniMax-M3.1-Flash"
    max_attempts = max(1, int(settings.MINIMAX_RETRY_MAX_ATTEMPTS))
    timeout_seconds = max(15, int(settings.MINIMAX_TIMEOUT_SECONDS))
    effort = reasoning_effort_for(purpose)

    client = openai.OpenAI(
        api_key=settings.MINIMAX_API_KEY,
        base_url=settings.MINIMAX_BASE_URL,
        timeout=float(timeout_seconds),
        max_retries=0,
    )

    completion = None
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        request: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": settings.MINIMAX_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "extra_body": {"reasoning_split": True},
        }
        if effort is not None:
            request["reasoning_effort"] = effort
        started = time.monotonic()
        try:
            completion = client.chat.completions.create(**request)
            usage = _usage_fields(completion)
            _record_usage(purpose, usage)
            logger.info(
                "MiniMax usage purpose=%s model=%s effort=%s prompt_tokens=%d cached_tokens=%d "
                "completion_tokens=%d reasoning_tokens=%d seconds=%.1f attempt=%d/%d",
                purpose,
                model_name,
                effort or "default",
                usage["prompt_tokens"],
                usage["cached_tokens"],
                usage["completion_tokens"],
                usage["reasoning_tokens"],
                time.monotonic() - started,
                attempt,
                max_attempts,
            )
            break
        except openai.APITimeoutError as exc:
            if attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, None)
                logger.warning(
                    "MiniMax timeout attempt=%d/%d retrying in %.1fs",
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            raise LLMGenerationError("MiniMax timeout during generation.") from exc
        except openai.APIStatusError as exc:
            status = getattr(exc, "status_code", 0) or 0
            error_body = _compact_error_text(str(getattr(exc, "message", "") or exc))[:300]
            lower_body = error_body.lower()
            if (
                status == 400
                and effort is not None
                and "reasoning" in lower_body
                and "effort" in lower_body
            ):
                # Models other than M3.1-Flash may reject the field: resend without it and do
                # not count the rejected request as an attempt.
                logger.warning(
                    "MiniMax model=%s rejected reasoning_effort=%s; retrying without it: %s",
                    model_name,
                    effort,
                    error_body,
                )
                effort = None
                attempt -= 1
                continue
            retryable = _is_retryable_http_error(status, error_body)
            if retryable and attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, _retry_after_from_openai_error(exc))
                logger.warning(
                    "MiniMax HTTP %d attempt=%d/%d retrying in %.1fs body=%s",
                    status,
                    attempt,
                    max_attempts,
                    delay,
                    error_body or "<empty>",
                )
                time.sleep(delay)
                continue
            logger.error("MiniMax HTTP %d body=%s", status, error_body or "<empty>")
            raise LLMGenerationError("MiniMax HTTP error during generation.") from exc
        except openai.APIConnectionError as exc:
            if attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, None)
                logger.warning(
                    "MiniMax network error attempt=%d/%d retrying in %.1fs: %s",
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                time.sleep(delay)
                continue
            raise LLMGenerationError("MiniMax network error during generation.") from exc

    if completion is None:
        raise LLMGenerationError("MiniMax generation returned no response.")

    content_text = _extract_text_from_openai_completion(completion)
    return _extract_json_payload(content_text)


def _is_minimax_configured() -> bool:
    return bool(settings.MINIMAX_BASE_URL and settings.MINIMAX_API_KEY)


def _call_llm_json(prompt: str, *, purpose: str = "default") -> dict[str, Any]:
    if not _is_minimax_configured():
        raise LLMGenerationError(
            "MiniMax is not configured (MINIMAX_BASE_URL / MINIMAX_API_KEY missing)."
        )
    return _call_minimax_json(prompt, purpose=purpose)


def generate_summary_json(
    *,
    source_platform: str,
    channel_name: str,
    speaker_name: str,
    video_title: str,
    published_at: str,
    source_url: str,
    transcript: str,
    output_language: str | None = None,
) -> dict[str, Any]:
    prompt = _build_prompt(
        SUMMARY_PROMPT_TEMPLATE,
        source_platform=source_platform,
        channel_name=channel_name,
        speaker_name=speaker_name,
        video_title=video_title,
        published_at=published_at,
        source_url=source_url,
        transcript=transcript,
        output_language=(output_language or settings.LLM_DEFAULT_OUTPUT_LANGUAGE or "tr"),
    )
    return _call_llm_json(prompt, purpose="summary")


def generate_classification_json(
    *,
    source_platform: str,
    channel_name: str,
    speaker_name: str,
    video_title: str,
    published_at: str,
    source_url: str,
    transcript: str,
    output_language: str | None = None,
) -> dict[str, Any]:
    prompt = _build_prompt(
        CLASSIFICATION_PROMPT_TEMPLATE,
        source_platform=source_platform,
        channel_name=channel_name,
        speaker_name=speaker_name,
        video_title=video_title,
        published_at=published_at,
        source_url=source_url,
        transcript=transcript,
        output_language=(output_language or settings.LLM_DEFAULT_OUTPUT_LANGUAGE or "tr"),
    )
    return _call_llm_json(prompt, purpose="classification")


def generate_economic_thesis_json(
    *,
    source_platform: str,
    channel_name: str,
    speaker_name: str,
    video_title: str,
    published_at: str,
    source_url: str,
    transcript: str,
    output_language: str | None = None,
) -> dict[str, Any]:
    prompt = _build_prompt(
        ECONOMIC_THESIS_PROMPT_TEMPLATE,
        source_platform=source_platform,
        channel_name=channel_name,
        speaker_name=speaker_name,
        video_title=video_title,
        published_at=published_at,
        source_url=source_url,
        transcript=transcript,
        output_language=(output_language or settings.LLM_DEFAULT_OUTPUT_LANGUAGE or "tr"),
    )
    return _call_llm_json(prompt, purpose="economic_thesis")


_CONTENT_MAX_ATTEMPTS = 2


def generate_analysis_json(
    *,
    source_platform: str,
    channel_name: str,
    speaker_name: str,
    video_title: str,
    published_at: str,
    source_url: str,
    transcript: str,
    output_language: str | None = None,
    channel_primary_topic: str = "",
) -> dict[str, Any]:
    """Single merged call that returns both summary and classification.

    Retries once if the payload comes back without key_points, since an empty
    highlights list written to the DB would make needs_summary=False on all
    future pipeline runs, permanently skipping the video.
    """
    prompt = _build_prompt(
        ANALYSIS_PROMPT_TEMPLATE,
        source_platform=source_platform,
        channel_name=channel_name,
        speaker_name=speaker_name,
        video_title=video_title,
        published_at=published_at,
        source_url=source_url,
        transcript=transcript,
        output_language=(output_language or settings.LLM_DEFAULT_OUTPUT_LANGUAGE or "tr"),
        channel_primary_topic=channel_primary_topic,
    )
    payload: dict[str, Any] = {}
    for attempt in range(1, _CONTENT_MAX_ATTEMPTS + 1):
        payload = _call_llm_json(prompt, purpose="analysis")
        key_points = payload.get("key_points")
        if isinstance(key_points, list) and key_points:
            return payload
        if attempt < _CONTENT_MAX_ATTEMPTS:
            logger.warning(
                "LLM returned empty/missing key_points (attempt %d/%d), retrying",
                attempt,
                _CONTENT_MAX_ATTEMPTS,
            )
    return payload


def generate_market_inference_json(
    *,
    topic_key: str,
    topic_label: str,
    sources_block: str,
    prev_section: str = "",
    agenda: bool = False,
) -> dict[str, Any]:
    """Call LLM for a single topic's market inference. Returns the parsed JSON dict.

    ``agenda`` topics are shown as context, so their summary must not state a direction.
    """
    if not _is_minimax_configured():
        raise LLMGenerationError(
            "MiniMax is not configured (MINIMAX_BASE_URL / MINIMAX_API_KEY missing)."
        )

    prompt = (
        MARKET_INFERENCE_PROMPT_TEMPLATE
        .replace("{{topic_key}}", topic_key)
        .replace("{{topic_label}}", topic_label)
        .replace("{{sources_block}}", sources_block)
        .replace("{{prev_section}}", prev_section)
        .replace(
            "{{summary_rules}}",
            MARKET_INFERENCE_SUMMARY_RULES + (MARKET_INFERENCE_AGENDA_RULES if agenda else ""),
        )
    )

    _VALID_DIRECTIONS = {"up", "down", "sideways", "mixed"}
    _MAX_ATTEMPTS = 3

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            payload = _call_llm_json(prompt, purpose="market_inference")
        except LLMGenerationError as exc:
            if attempt < _MAX_ATTEMPTS:
                logger.warning(
                    "Inference JSON parse error topic=%s attempt=%d/%d, retrying: %s",
                    topic_key, attempt, _MAX_ATTEMPTS, exc,
                )
                continue
            raise

        direction = payload.get("direction", "")
        confidence = float(payload.get("confidence", 0.0))

        # Validate: confidence<0.5 + directional is invalid
        if direction in ("up", "down") and confidence < 0.5:
            logger.warning(
                "Inference validation failed topic=%s direction=%s confidence=%.2f (attempt %d), retrying",
                topic_key,
                direction,
                confidence,
                attempt,
            )
            if attempt < _MAX_ATTEMPTS:
                continue

        if direction not in _VALID_DIRECTIONS:
            payload["direction"] = "mixed"

        return payload

    return payload


def generate_outlook_json(
    *,
    video_title: str,
    speaker_name: str,
    published_at: str,
    items_block: str,
) -> dict[str, Any]:
    """Classify the speaker's forward-looking direction per instrument mention."""
    prompt = _replace_placeholders(
        OUTLOOK_PROMPT_TEMPLATE,
        {
            "video_title": video_title,
            "speaker_name": speaker_name,
            "published_at": published_at,
            "items": items_block,
        },
    )
    return _call_llm_json(prompt, purpose="outlook")


def generate_quote_clean_json(*, items_block: str) -> dict[str, Any]:
    """Lightly edited, readable versions of verbatim transcript quotes."""
    prompt = _replace_placeholders(QUOTE_CLEAN_PROMPT_TEMPLATE, {"items": items_block})
    return _call_llm_json(prompt, purpose="quote_clean")


def generate_participants_json(
    *,
    channel_name: str,
    video_title: str,
    title_names: str,
    transcript_intro: str,
) -> dict[str, Any]:
    """Who hosts and who is a guest in a multi-guest channel's video."""
    prompt = _replace_placeholders(
        PARTICIPANTS_PROMPT_TEMPLATE,
        {
            "channel_name": channel_name,
            "video_title": video_title,
            "title_names": title_names,
            "transcript_intro": transcript_intro,
        },
    )
    return _call_llm_json(prompt, purpose="participants")
