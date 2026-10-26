"""Readable version of the outlook quote.

`outlook_quote` is copied word for word from the auto-generated transcript, so
it carries speech fillers, false starts and recognition errors ("Şimdi şöyle
olacak, iyi olacak falan yani..."). This step asks the LLM for a lightly
edited sentence that keeps the speaker's own words, numbers and certainty, and
rejects any edit that changes the numbers or drifts away from the original
words. The verbatim quote stays stored; clients show the clean one when present.
"""

import re
from typing import Any

from src.llm import service as llm_service

BATCH = 15
_CONTEXT_CHARS = 300
_MAX_CHARS = 220
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_MAX_NEW_WORDS = 1
_NEGATIONS = frozenset({"değil", "yok", "hiç", "hayır"})
# Turkish verb negation lives in a suffix ("düşünmüyorum", "kalmadığı", "artmaz").
_NEGATIVE_SUFFIX = re.compile(
    r"m[ıiuü]yor|m[ae]d[ıi]|m[ae][zy]|m[ae]y[ae]c[ae]k|m[ae]m[ıi]ş|m[ae]d[ıi]ğ|m[ae]m$"
)
# Direction words: if the original has one, the clean sentence must keep it.
_DIRECTION_STEMS = (
    "yukarı", "aşağı", "yüksel", "düş", "art", "azal", "gerile", "ralli", "hedef",
    "alım", "satış", "pozitif", "negatif", "olumlu", "olumsuz", "iyimser", "kötümser",
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _numbers(text: str) -> list[str]:
    return sorted(_NUMBER.findall(text))


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


def _count(words: list[str], vocab: frozenset[str]) -> int:
    return sum(1 for w in words if w in vocab)


def _negative_verbs(words: list[str]) -> int:
    return sum(1 for w in words if _NEGATIVE_SUFFIX.search(w))


def quote_context(quote: str, excerpt: str) -> str:
    """Transcript text around the quote, so a sentence that starts mid-way can be read."""
    if not excerpt:
        return ""
    probe = quote.strip(" .…\"'“”")[:25].casefold()
    pos = excerpt.casefold().find(probe) if probe else -1
    if pos < 0:
        return excerpt[:_CONTEXT_CHARS * 2]
    return excerpt[max(0, pos - _CONTEXT_CHARS) : pos + len(quote) + _CONTEXT_CHARS]


def accept(original: str, clean: str, context: str = "") -> str | None:
    """The clean sentence if it is a light edit of the original, else None."""
    clean = " ".join(clean.split()).strip()
    if not clean or len(clean) > _MAX_CHARS:
        return None
    if _numbers(clean) != _numbers(original):
        return None
    before, after = _words(original), _words(clean)
    if not after or _count(after, _NEGATIONS) != _count(before, _NEGATIONS):
        return None
    if _negative_verbs(after) != _negative_verbs(before):
        return None
    for stem in _DIRECTION_STEMS:
        if any(w.startswith(stem) for w in before) and not any(w.startswith(stem) for w in after):
            return None
    known = set(before) | set(_words(context))
    if sum(1 for w in after if w not in known) > _MAX_NEW_WORDS:
        return None
    return clean


def _items_block(items: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[id={item['id']}] Araç: {item.get('instrument', '')}\n"
        f"Cümle: {item['quote']}\n"
        f"Bağlam: {item.get('context') or '(yok)'}"
        for item in items
    )


def clean_quotes(items: list[dict[str, Any]]) -> dict[int, str | None]:
    """items: {id, quote, context, instrument}. Returns {id: clean sentence or None}."""
    out: dict[int, str | None] = {}
    for start in range(0, len(items), BATCH):
        batch = items[start : start + BATCH]
        payload = llm_service.generate_quote_clean_json(items_block=_items_block(batch))
        answers: dict[int, str] = {}
        for row in payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            try:
                answers[int(row.get("id"))] = str(row.get("clean") or "")
            except (TypeError, ValueError):
                continue
        for item in batch:
            answer = answers.get(item["id"], "")
            out[item["id"]] = accept(item["quote"], answer, item.get("context") or "")
    return out
