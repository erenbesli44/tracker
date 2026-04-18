"""Gemini-backed transcript analysis helpers."""

import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from src.config import settings

logger = logging.getLogger(__name__)
from src.llm.prompts import (
    ANALYSIS_PROMPT_TEMPLATE,
    CLASSIFICATION_PROMPT_TEMPLATE,
    ECONOMIC_THESIS_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
)

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
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


class LLMGenerationError(Exception):
    """Raised when structured generation cannot be completed."""


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


def _extract_text_from_gemini_response(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LLMGenerationError("Gemini response has no candidates.")

    content = candidates[0].get("content")
    if not isinstance(content, dict):
        raise LLMGenerationError("Gemini response content is missing.")

    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise LLMGenerationError("Gemini response parts are missing.")

    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part["text"])

    if not chunks:
        raise LLMGenerationError("Gemini response has no text part.")
    return "\n".join(chunks).strip()


def _compact_error_text(value: str) -> str:
    return " ".join(value.split()).strip()


def _read_http_error_body(exc: HTTPError, max_chars: int = 300) -> str:
    try:
        raw = exc.read()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return ""
    return _compact_error_text(text)[:max_chars]


def _parse_retry_after_seconds(exc: HTTPError) -> float | None:
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, seconds)


def _is_retryable_http_error(status_code: int, body: str) -> bool:
    if status_code in _RETRYABLE_HTTP_CODES:
        return True
    lower_body = body.lower()
    return any(phrase in lower_body for phrase in _RETRYABLE_ERROR_PHRASES)


def _retry_delay_seconds(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return min(retry_after, settings.GEMINI_RETRY_MAX_DELAY_SECONDS)
    base = max(0.1, float(settings.GEMINI_RETRY_BASE_DELAY_SECONDS))
    max_delay = max(base, float(settings.GEMINI_RETRY_MAX_DELAY_SECONDS))
    return min(max_delay, base * (2 ** (attempt - 1)))


def _extract_text_from_openai_response(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMGenerationError("OpenAI-compatible response has no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise LLMGenerationError("OpenAI-compatible response has no content.")
    return content.strip()


def _call_local_llm_json(prompt: str) -> dict[str, Any]:
    if not settings.LOCAL_LLM_BASE_URL or not settings.LOCAL_LLM_TOKEN:
        raise LLMGenerationError("LOCAL_LLM_BASE_URL and LOCAL_LLM_TOKEN must be configured.")

    model_name = settings.LOCAL_LLM_MODEL or "minimax-m2.7"
    max_attempts = max(1, int(settings.GEMINI_RETRY_MAX_ATTEMPTS))
    timeout_seconds = max(15, int(settings.GEMINI_TIMEOUT_SECONDS))

    request_payload = {
        "model": model_name,
        "stream": False,
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
    body = json.dumps(request_payload).encode("utf-8")
    endpoint = settings.LOCAL_LLM_BASE_URL.rstrip("/")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.LOCAL_LLM_TOKEN}",
        },
        method="POST",
    )

    raw_response = ""
    for attempt in range(1, max_attempts + 1):
        try:
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urlopen(request, timeout=timeout_seconds, context=ctx) as response:  # nosec B310
                raw_response = response.read().decode("utf-8")
            logger.info("Local LLM call succeeded model=%s attempt=%d/%d", model_name, attempt, max_attempts)
            break
        except TimeoutError as exc:
            if attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, None)
                time.sleep(delay)
                continue
            raise LLMGenerationError("Local LLM timeout.") from exc
        except HTTPError as exc:
            error_body = _read_http_error_body(exc)
            retryable = _is_retryable_http_error(exc.code, error_body)
            if retryable and attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, _parse_retry_after_seconds(exc))
                logger.warning("Local LLM HTTP %d attempt=%d/%d retrying in %.1fs", exc.code, attempt, max_attempts, delay)
                time.sleep(delay)
                continue
            logger.error("Local LLM HTTP %d; body=%s", exc.code, error_body)
            raise LLMGenerationError("Local LLM HTTP error.") from exc
        except URLError as exc:
            if attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, None)
                time.sleep(delay)
                continue
            raise LLMGenerationError("Local LLM network error.") from exc

    if not raw_response:
        raise LLMGenerationError("Local LLM returned no response.")

    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise LLMGenerationError("Local LLM response is not valid JSON.") from exc

    content_text = _extract_text_from_openai_response(payload)
    return _extract_json_payload(content_text)


def _is_local_llm_configured() -> bool:
    return bool(
        settings.ENVIRONMENT == "local"
        and settings.LOCAL_LLM_BASE_URL
        and settings.LOCAL_LLM_TOKEN
    )


def _call_gemini_json(prompt: str) -> dict[str, Any]:
    if not settings.GEMINI_API_KEY:
        raise LLMGenerationError("GEMINI_API_KEY is not configured.")

    model_name = settings.GEMINI_MODEL.strip() or DEFAULT_GEMINI_MODEL
    encoded_model = quote(model_name, safe="")

    max_attempts = max(1, int(settings.GEMINI_RETRY_MAX_ATTEMPTS))
    timeout_seconds = max(15, int(settings.GEMINI_TIMEOUT_SECONDS))

    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{encoded_model}:generateContent?key={settings.GEMINI_API_KEY}"
    )
    request_payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    body = json.dumps(request_payload).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    raw_response = ""
    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
                raw_response = response.read().decode("utf-8")
                logger.info(
                    "Gemini call succeeded with model=%s attempt=%d/%d",
                    model_name,
                    attempt,
                    max_attempts,
                )
                break
        except TimeoutError as exc:
            if attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, None)
                logger.warning(
                    "Gemini timeout with model=%s attempt=%d/%d; retrying in %.1fs",
                    model_name,
                    attempt,
                    max_attempts,
                    delay,
                )
                time.sleep(delay)
                continue
            logger.warning("Gemini timeout with model=%s", model_name)
            raise LLMGenerationError("Gemini timeout during generation.") from exc
        except HTTPError as exc:
            error_body = _read_http_error_body(exc)
            retry_after = _parse_retry_after_seconds(exc)
            retryable = _is_retryable_http_error(exc.code, error_body)
            if retryable and attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, retry_after)
                logger.warning(
                    "Gemini HTTP %d with model=%s attempt=%d/%d; retrying in %.1fs; body=%s",
                    exc.code,
                    model_name,
                    attempt,
                    max_attempts,
                    delay,
                    error_body or "<empty>",
                )
                time.sleep(delay)
                continue
            logger.error(
                "Gemini HTTP %d with model=%s; body=%s",
                exc.code,
                model_name,
                error_body or "<empty>",
            )
            raise LLMGenerationError("Gemini HTTP error during generation.") from exc
        except URLError as exc:
            if attempt < max_attempts:
                delay = _retry_delay_seconds(attempt, None)
                logger.warning(
                    "Gemini network error with model=%s attempt=%d/%d; retrying in %.1fs: %s",
                    model_name,
                    attempt,
                    max_attempts,
                    delay,
                    exc.reason,
                )
                time.sleep(delay)
                continue
            logger.error("Gemini network error: %s", exc.reason)
            raise LLMGenerationError("Gemini network error during generation.") from exc
        except Exception as exc:
            logger.error("Unexpected Gemini error: %s", exc)
            raise LLMGenerationError("Unexpected Gemini generation error.") from exc

    if not raw_response:
        raise LLMGenerationError("Gemini generation returned no response.")

    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise LLMGenerationError("Gemini response is not valid JSON.") from exc

    content_text = _extract_text_from_gemini_response(payload)
    return _extract_json_payload(content_text)


def _call_llm_json(prompt: str) -> dict[str, Any]:
    if _is_local_llm_configured():
        return _call_local_llm_json(prompt)
    return _call_llm_json(prompt)


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
    return _call_llm_json(prompt)


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
    return _call_llm_json(prompt)


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
    return _call_llm_json(prompt)


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
    """Single merged call that returns both summary and classification."""
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
    return _call_llm_json(prompt)
