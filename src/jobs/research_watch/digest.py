"""Digest schema, validation and mechanical checks for research posts (OI-041).

A digest that fails a check is stored with its problems and waits for review; it is never
served automatically. The checks are deliberately strict: a false alarm costs a review, a
missed invented number costs a wrong public claim.
"""

import html
import re
from typing import Any

from src.llm.prompts import RESEARCH_DIGEST_PROMPT_VERSION

DIGEST_VERSION = RESEARCH_DIGEST_PROMPT_VERSION
ALLOWED_TOPICS = (
    "doviz-kur",
    "altin",
    "gumus",
    "bist-turk-piyasalari",
    "kripto-paralar",
    "amerikan-piyasalari",
    "petrol-enerji",
    "faiz-para-politikasi",
    "enflasyon",
)
TEXT_FIELDS = ("headline", "why_now", "so_what", "not_said", "quote", "basis")
LIMITS = {"headline": 120, "finding": 220}

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# A post by bank staff is not the bank's forecast, and nothing here is a trade call.
_FORBIDDEN = (
    re.compile(r"\bTCMB\b[^.]{0,40}\b(bekliyor|öngörüyor|tahmin ediyor)", re.IGNORECASE),
    re.compile(r"\b(al|sat|alım|satım)\s+(sinyali|fırsatı|önerisi)", re.IGNORECASE),
)


class DigestFormatError(ValueError):
    """The model output does not have the digest shape."""


def _str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DigestFormatError(f"missing or empty field: {key}")
    return value.strip()


def validate_digest(raw: Any) -> dict:
    """Normalised digest dict, or DigestFormatError when the shape is wrong."""
    if not isinstance(raw, dict):
        raise DigestFormatError("digest is not a JSON object")
    digest = {key: _str(raw, key) for key in TEXT_FIELDS}
    findings = raw.get("findings")
    if not isinstance(findings, list):
        raise DigestFormatError("findings is not a list")
    findings = [f.strip() for f in findings if isinstance(f, str) and f.strip()]
    if not 1 <= len(findings) <= 3:
        raise DigestFormatError(f"findings has {len(findings)} items, expected 2-3")
    digest["findings"] = findings
    terms = raw.get("terms") or []
    digest["terms"] = [
        {"term": t["term"].strip(), "meaning": t["meaning"].strip()}
        for t in terms
        if isinstance(t, dict)
        and isinstance(t.get("term"), str)
        and isinstance(t.get("meaning"), str)
        and t["term"].strip()
        and t["meaning"].strip()
    ][:3]
    topics = raw.get("topics") or []
    digest["topics"] = [t for t in dict.fromkeys(topics) if t in ALLOWED_TOPICS][:3]
    return digest


def _normalise(text: str) -> str:
    text = html.unescape(text)
    for curly, plain in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("\xa0", " ")):
        text = text.replace(curly, plain)
    return re.sub(r"\s+", " ", text).strip()


def _in_source(number: str, source: str) -> bool:
    pattern = rf"(?<![\d.,]){re.escape(number)}(?![\d]|[.,]\d)"
    return re.search(pattern, source) is not None


def check_digest(digest: dict, body_text: str) -> list[str]:
    """Problems that keep a digest from being served automatically (empty list = passes)."""
    problems: list[str] = []
    source = _normalise(body_text)
    texts = [digest[k] for k in TEXT_FIELDS] + digest["findings"]
    texts += [t["meaning"] for t in digest["terms"]]

    numbers = {n for text in texts for n in _NUMBER.findall(_normalise(text))}
    missing = sorted(n for n in numbers if not _in_source(n, source))
    if missing:
        problems.append(f"numbers not in source: {', '.join(missing)}")

    quote = _normalise(digest["quote"]).strip("\"'“”…. ")
    if len(quote) < 20 or quote not in source:
        problems.append("quote is not a verbatim sentence of the source")

    if len(digest["findings"]) < 2:
        problems.append("fewer than 2 findings")
    if len(digest["headline"]) > LIMITS["headline"]:
        problems.append(f"headline longer than {LIMITS['headline']} chars")
    if any(len(f) > LIMITS["finding"] for f in digest["findings"]):
        problems.append(f"a finding is longer than {LIMITS['finding']} chars")

    for pattern in _FORBIDDEN:
        if any(pattern.search(text) for text in texts):
            problems.append(f"forbidden wording: {pattern.pattern}")
    return problems
