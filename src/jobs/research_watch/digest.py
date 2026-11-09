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
_FOOTNOTE = re.compile(r"\[\d+\]")
_PERCENT = re.compile(r"%\s?(?=\d)")
_SENTENCE_END = (".", "!", "?")
# A post by bank staff is not the bank's forecast, and nothing here is a trade call.
_FORBIDDEN = (
    re.compile(
        r"\b(TCMB|Merkez Bankası|Banka)\b[^.]{0,40}\b(bekliyor|öngörüyor|tahmin ediyor)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(TCMB|Merkez Bankası)['’]?(nın|nin)? (beklentisi|tahmini|öngörüsü)", re.IGNORECASE
    ),
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
        raise DigestFormatError(f"findings has {len(findings)} items, expected 1-3")
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
    text = _PERCENT.sub("yüzde ", _FOOTNOTE.sub("", text))
    return re.sub(r"\s+", " ", text).strip()


def _number_in_source(text: str, match: re.Match, source: str) -> bool:
    """Long or decimal numbers must occur as such. A one- or two-digit integer occurs too
    often by chance (chart numbers, dates, counts), so it must occur next to the same word
    as in the digest: "57 milyar", "binde 3", "17 Eylül"."""
    number = match.group()
    if len(number) > 2 or not number.isdigit():
        return re.search(rf"(?<![\d.,]){re.escape(number)}(?![\d]|[.,]\d)", source) is not None
    before = re.search(r"(\w+)\s+$", text[: match.start()])
    after = re.match(r"\s+(\w+)", text[match.end() :])
    phrases = []
    if before:
        phrases.append(rf"(?<!\w){re.escape(before.group(1))} {number}(?![\d]|[.,]\d)")
    if after:
        phrases.append(rf"(?<![\d.,]){number} {re.escape(after.group(1))}")
    return any(re.search(p, source, re.IGNORECASE) for p in phrases)


def _quote_is_sentence(quote: str, body_text: str) -> bool:
    """The quote is one or more whole sentences of one paragraph of the post."""
    quote = _normalise(quote).strip("\"' ")
    if len(quote) < 20 or not quote.endswith(_SENTENCE_END) or quote.startswith(("…", "...")):
        return False
    pattern = r"(?:^|(?<=[.!?:] ))" + re.escape(quote) + r"(?=\s|$)"
    return any(re.search(pattern, _normalise(line)) for line in body_text.splitlines())


def check_digest(digest: dict, body_text: str) -> list[str]:
    """Problems that keep a digest from being served automatically (empty list = passes)."""
    problems: list[str] = []
    source = _normalise(body_text)
    texts = [digest[k] for k in TEXT_FIELDS] + digest["findings"]
    texts += [t["meaning"] for t in digest["terms"]]

    missing = set()
    for text in texts:
        text = _normalise(text)
        for match in _NUMBER.finditer(text):
            if not _number_in_source(text, match, source):
                missing.add(match.group())
    if missing:
        problems.append(f"numbers not in source: {', '.join(sorted(missing))}")

    if not _quote_is_sentence(digest["quote"], body_text):
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
