"""Gemini-backed transcript analysis helpers."""

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.config import settings

logger = logging.getLogger(__name__)
from src.llm.prompts import (
    ANALYSIS_PROMPT_TEMPLATE,
    CLASSIFICATION_PROMPT_TEMPLATE,
    SUMMARY_PROMPT_TEMPLATE,
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


def _call_gemini_json(prompt: str) -> dict[str, Any]:
    if not settings.GEMINI_API_KEY:
        raise LLMGenerationError("GEMINI_API_KEY is not configured.")

    model_candidates = []
    for model_name in [
        settings.GEMINI_MODEL,
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]:
        candidate = model_name.strip()
        if candidate and candidate not in model_candidates:
            model_candidates.append(candidate)

    raw_response: str | None = None
    last_error: Exception | None = None

    timeout_seconds = max(15, int(settings.GEMINI_TIMEOUT_SECONDS))

    for model_name in model_candidates:
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model_name}:generateContent?key={settings.GEMINI_API_KEY}"
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

        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
                raw_response = response.read().decode("utf-8")
                logger.info("Gemini call succeeded with model=%s", model_name)
                break
        except TimeoutError as exc:
            logger.warning("Gemini timeout with model=%s", model_name)
            last_error = exc
            continue
        except HTTPError as exc:
            last_error = exc
            if exc.code in {400, 404}:
                logger.warning("Gemini HTTP %d with model=%s, trying next", exc.code, model_name)
                continue
            logger.error("Gemini HTTP %d with model=%s", exc.code, model_name)
            raise LLMGenerationError("Gemini HTTP error during generation.") from exc
        except URLError as exc:
            logger.error("Gemini network error: %s", exc.reason)
            raise LLMGenerationError("Gemini network error during generation.") from exc
        except Exception as exc:
            logger.error("Unexpected Gemini error: %s", exc)
            raise LLMGenerationError("Unexpected Gemini generation error.") from exc

    if raw_response is None:
        logger.error("All Gemini model candidates exhausted, last_error=%s", last_error)
        if last_error is not None:
            raise LLMGenerationError("Gemini model selection failed.") from last_error
        raise LLMGenerationError("Gemini generation returned no response.")

    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise LLMGenerationError("Gemini response is not valid JSON.") from exc

    content_text = _extract_text_from_gemini_response(payload)
    return _extract_json_payload(content_text)


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
    return _call_gemini_json(prompt)


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
    return _call_gemini_json(prompt)


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
    )
    return _call_gemini_json(prompt)
