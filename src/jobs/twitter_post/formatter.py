"""Build Twitter thread texts from a video summary + topic mentions.

Thread structure
────────────────
Tweet 1  — Opener:  video title · channel · highlights bullets · URL
Tweet 2+ — One per TopicMention: emoji · topic name · summary · sentiment
"""

import json
import re

TWEET_MAX = 280
TCO_URL_LEN = 23  # Twitter wraps every URL to 23 weighted chars via t.co

# ── Topic emoji map ─────────────────────────────────────────────────────────
TOPIC_EMOJI: dict[str, str] = {
    "bist-turk-piyasalari": "📈",
    "altin":                 "🥇",
    "gumus":                 "🪙",
    "doviz-kur":             "💵",
    "kripto-paralar":        "🪙",
    "amerikan-piyasalari":   "🇺🇸",
    "petrol-enerji":         "🛢️",
    "faiz-para-politikasi":  "🏦",
    "enflasyon":             "📊",
    "ic-siyaset":            "🏛️",
    "jeopolitik":            "🌍",
    "ekonomi":               "💹",
    "siyaset":               "⚖️",
    "spor":                  "⚽",
    "teknoloji":             "💻",
}
_DEFAULT_EMOJI = "📋"

# ── Sentiment labels ────────────────────────────────────────────────────────
SENTIMENT_LABEL: dict[str, str] = {
    "bullish": "📈 Yükseliş",
    "bearish": "📉 Düşüş",
    "neutral": "➡️ Nötr",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _trim(text: str, budget: int) -> str:
    """Trim text to budget chars, appending … if truncated."""
    text = text.strip()
    if len(text) > budget:
        return text[: budget - 1].rstrip() + "…"
    return text


def _weighted_len(text: str) -> int:
    """Approximate Twitter weighted char count (each URL counts as 23)."""
    length = len(text)
    for url in re.findall(r"https?://\S+", text):
        length += TCO_URL_LEN - len(url)
    return length


def _fits(text: str, limit: int = TWEET_MAX) -> bool:
    return _weighted_len(text) <= limit


# ── Opener tweet ────────────────────────────────────────────────────────────

def _build_opener(
    title: str,
    channel_name: str | None,
    highlights: list[str],
    has_thread: bool,
) -> str:
    """
    Format:
        📺 {title}
        🎙️ {channel}

        📌 Öne Çıkanlar:
        ▪️ {h1}
        ▪️ {h2}

        🧵 (only when there are thread tweets)

    Progressively trims content until the tweet fits 280 weighted chars.
    """
    thread_suffix = "\n\n🧵" if has_thread else ""
    ch = (channel_name or "").strip()
    channel_line = f"🎙️ {ch}\n\n" if ch else "\n"

    bullets_raw = [h.strip() for h in highlights if h.strip()]

    def _compose(title_t: str, bullets_t: list[str]) -> str:
        if bullets_t:
            block = "📌 Öne Çıkanlar:\n" + "\n".join(f"▪️ {b}" for b in bullets_t)
            return f"📺 {title_t}\n{channel_line}{block}{thread_suffix}"
        return f"📺 {title_t}\n{channel_line.rstrip()}{thread_suffix}"

    title_t = _trim(title or "—", 70)
    for attempt in (
        [_trim(b, 60) for b in bullets_raw[:2]],  # 2 bullets × 60
        [_trim(b, 50) for b in bullets_raw[:1]],  # 1 bullet × 50
        [_trim(b, 30) for b in bullets_raw[:1]],  # 1 very short bullet
        [],                                        # no bullets
    ):
        draft = _compose(title_t, attempt)
        if _fits(draft):
            return draft

    # Last resort: trim title until it fits
    while not _fits(_compose(title_t, [])) and len(title_t) > 20:
        title_t = _trim(title_t, len(title_t) - 5)
    return _compose(title_t, [])


# ── Topic tweet ──────────────────────────────────────────────────────────────

def _build_topic_tweet(
    topic_name: str,
    topic_slug: str,
    summary: str,
    sentiment: str | None,
) -> str:
    """
    Format:
        {emoji} {topic_name}

        {summary}

        {sentiment_label}
    """
    emoji = TOPIC_EMOJI.get(topic_slug, _DEFAULT_EMOJI)
    header = f"{emoji} {topic_name}\n\n"
    sentiment_line = ""
    if sentiment and sentiment.lower() in SENTIMENT_LABEL:
        sentiment_line = f"\n\n{SENTIMENT_LABEL[sentiment.lower()]}"

    summary_budget = TWEET_MAX - len(header) - len(sentiment_line)
    summary_trimmed = _trim(summary or "—", max(summary_budget, 30))

    return f"{header}{summary_trimmed}{sentiment_line}"


# ── Public API ───────────────────────────────────────────────────────────────

def build_thread(
    *,
    title: str,
    video_url: str,
    channel_name: str | None,
    highlights_json: str | None,
    topic_mentions: list[dict],
) -> list[str]:
    """Return a list of tweet texts forming a thread.

    Each dict in topic_mentions must have:
        name (str), slug (str), summary (str), sentiment (str | None)

    If topic_mentions is empty, returns a single tweet (no thread).
    """
    highlights: list[str] = []
    if highlights_json:
        try:
            parsed = json.loads(highlights_json)
            if isinstance(parsed, list):
                highlights = [str(h) for h in parsed if h]
        except (json.JSONDecodeError, TypeError):
            pass

    has_thread = bool(topic_mentions)
    tweets: list[str] = [
        _build_opener(title, channel_name, highlights, has_thread)
    ]

    for tm in topic_mentions:
        tweet = _build_topic_tweet(
            topic_name=tm.get("name", "—"),
            topic_slug=tm.get("slug", ""),
            summary=tm.get("summary", "—"),
            sentiment=tm.get("sentiment"),
        )
        tweets.append(tweet)

    return tweets
