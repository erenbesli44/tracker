"""Deterministic cues for forward-looking price views in a transcript (no LLM).

Two uses:
- ``rank_chunks`` keeps the parts of a long mention window that most likely hold the
  speaker's expectation, so the outlook step does not only read the window's first minutes
  (a view stated at 10:59 of a mention that starts at 0:54 was never shown to the model).
- ``missed_instruments`` flags tracked instruments that have a forward statement in the
  transcript but no mention at all (a 25-second "altında 4.500 dolara kadar çıkış
  bekliyorum" near the end of a video produced no altın mention).

Both are recall aids: the LLM still decides whether there is a view.
"""

import re
from typing import Any

# Words a speaker uses when committing to an expectation.
STRONG_CUES = (
    "bekliyorum", "bekliyoruz", "beklerim", "beklemiyorum", "beklentim", "öngörüyorum",
    "tahmin ediyorum", "tahminim", "hedefim", "hedefimiz", "hedef ", "şaşırmayın",
    "göreceğiz", "görebiliriz", "çıkış bekl", "düşüş bekl", "yükseliş bekl",
    "zannediyorum", "iddiam", "iddialıyım",
)
# Future / possibility forms: weaker, they also appear in ordinary narration.
WEAK_CUES = (
    "çıkabilir", "düşebilir", "gelebilir", "inebilir", "gidebilir", "yükselecek", "düşecek",
    "gerileyecek", "çıkacak", "inecek", "devam edecek", "sürecek", "kadar çık", "kadar düş",
    "kadar in", "kadar geri", "boğa", "ayı piyasası", "yıl sonu", "sene sonu",
)

# Unambiguous instrument words. "dolar" alone is a price unit and "altında / altına" mean
# "below", so both are left out on purpose.
INSTRUMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "altin": re.compile(r"\baltın(?:ın|la)?\b"),
    "gumus": re.compile(r"\bgümüş"),
    "doviz-kur": re.compile(
        r"dolar\s*/?\s*tl\b|dolar kuru|usd\s*/?\s*try|\bkur(?:un|da)?\b"
    ),
    "bist-turk-piyasalari": re.compile(r"\bbist\b|borsa istanbul|xu\s?100|bist\s?100"),
    "kripto-paralar": re.compile(r"\bbitcoin|\bbtc\b"),
    "amerikan-piyasalari": re.compile(
        r"nasdaq|nazdak|s&p|wall street|dow jones|amerikan borsa|abd borsa"
    ),
    "petrol-enerji": re.compile(r"\bbrent|\bham petrol|\bpetrol(?:ün|de)?\b|\bwti\b"),
}

_TEASER_SECONDS = 90.0  # opening fragman: disjointed preview sentences, never a view
_CUE_AFTER_SECONDS = 30.0
_CUE_BEFORE_SECONDS = 10.0


def _lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def cue_score(text: str, slug: str | None = None) -> int:
    """How likely this text holds a forward view: strong cues 3, weak cues 1, instrument 2."""
    low = _lower(text)
    score = 3 * sum(low.count(c) for c in STRONG_CUES) + sum(low.count(c) for c in WEAK_CUES)
    pattern = INSTRUMENT_PATTERNS.get(slug or "")
    if score and pattern is not None and pattern.search(low):
        score += 2
    return score


def rank_chunks(chunks: list[str], max_chars: int, slug: str | None = None) -> list[int]:
    """Indexes of the chunks to keep within ``max_chars``, in their original order.

    The first chunk always stays (the topic is introduced there). The rest are taken by cue
    score, earlier first on ties, so a window without any cue keeps its head as before.
    """
    if not chunks:
        return []
    keep = [0]
    used = len(chunks[0])
    order = sorted(range(1, len(chunks)), key=lambda i: (-cue_score(chunks[i], slug), i))
    for i in order:
        if used + len(chunks[i]) + 3 > max_chars:
            if cue_score(chunks[i], slug) == 0:
                break  # only filler is left: keep the plain head, no scattered tail pieces
            continue
        keep.append(i)
        used += len(chunks[i]) + 3
    return sorted(keep)


def missed_instruments(
    segments: list[dict[str, Any]] | None, existing_slugs: set[str]
) -> dict[str, float]:
    """Tracked instruments with a forward statement in the transcript but no mention.

    Returns {slug: seconds of the first hit}. A hit is an instrument word with a strong cue
    from 10 s before to 30 s after it, outside the opening teaser.
    """
    if not segments:
        return {}
    rows = [
        (float(s.get("start", 0.0) or 0.0), _lower(str(s.get("text", ""))))
        for s in segments
        if str(s.get("text", "")).strip()
    ]
    cue_times = [t for t, text in rows if any(c in text for c in STRONG_CUES)]
    if not cue_times:
        return {}
    out: dict[str, float] = {}
    for slug, pattern in INSTRUMENT_PATTERNS.items():
        if slug in existing_slugs:
            continue
        for t, text in rows:
            if t < _TEASER_SECONDS or not pattern.search(text):
                continue
            if any(t - _CUE_BEFORE_SECONDS <= c <= t + _CUE_AFTER_SECONDS for c in cue_times):
                out[slug] = t
                break
    return out


def anchored_passages(
    segments: list[dict[str, Any]], slug: str | None, lo: float, hi: float, max_chars: int
) -> str:
    """Text outside the window [lo, hi] where the instrument is named next to a strong cue
    (a recap at the end of the video, an aside in another section). Teaser excluded."""
    pattern = INSTRUMENT_PATTERNS.get(slug or "")
    if pattern is None or max_chars <= 0:
        return ""
    rows = [
        (float(s.get("start", 0.0) or 0.0), str(s.get("text", "")).strip()) for s in segments
    ]
    rows = [(t, x) for t, x in rows if x]
    cue_times = [t for t, x in rows if any(c in _lower(x) for c in STRONG_CUES)]
    hits = [
        t for t, x in rows
        if t >= _TEASER_SECONDS and not lo <= t <= hi and pattern.search(_lower(x))
        and any(t - _CUE_BEFORE_SECONDS <= c <= t + _CUE_AFTER_SECONDS for c in cue_times)
    ]
    picked = [
        x for t, x in rows
        if not lo <= t <= hi
        and any(h - _CUE_BEFORE_SECONDS <= t <= h + _CUE_AFTER_SECONDS for h in hits)
    ]
    return " ".join(picked)[:max_chars]
