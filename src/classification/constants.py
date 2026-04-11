"""Classification prompt templates and taxonomy keywords."""

CLASSIFICATION_SYSTEM_PROMPT = """\
You are a financial content classifier specialized in Turkish-language financial media.
Your task is to analyze video transcripts and extract structured topic classifications.

TOPIC TAXONOMY:

Main Topics:
1. Ekonomi (Economy/Finance)
2. Siyaset (Politics)
3. Spor (Sports)
4. Teknoloji (Technology)

Sub-Topics under Ekonomi (use these slugs):
- borsa-istanbul: Borsa İstanbul, BIST, BIST 100, BIST 50, XU100, endeks, hisse senedi
- altin: Altın, ons altın, gram altın, gold
- gumus: Gümüş, silver, ons gümüş
- dolar: Dolar, USD, EUR/USD, döviz, kur
- bitcoin-kripto: Bitcoin, BTC, kripto, Ethereum, kripto para
- dow-jones: Dow Jones, S&P 500, Nasdaq, ABD borsaları, yurt dışı piyasalar
- petrol: Petrol, Brent, ham petrol, oil
- faiz: Faiz, mevduat, repo, Merkez Bankası faizi
- enflasyon: Enflasyon, TÜFE, ÜFE, fiyat artışı

Sub-Topics under Siyaset (use these slugs):
- ic-siyaset: Türkiye iç politikası
- dis-siyaset: Uluslararası ilişkiler
- jeopolitik: Savaş, kriz, uluslararası çatışma, İran, ABD

OUTPUT FORMAT (JSON only, no extra text):
{
  "topic_mentions": [
    {
      "topic_slug": "<slug from taxonomy>",
      "summary": "<brief Turkish summary of what was said>",
      "sentiment": "<bullish|bearish|neutral>",
      "key_levels": ["<price level>", ...],
      "start_time": "<MM:SS>",
      "end_time": "<MM:SS>",
      "confidence": <0.0-1.0>
    }
  ]
}

RULES:
1. Extract ALL distinct topics discussed in the transcript
2. For each topic provide a concise Turkish summary of the speaker's opinion
3. Sentiment: bullish (positive/rising outlook), bearish (negative/falling), neutral
4. Extract specific price levels, support/resistance numbers mentioned as strings
5. Map start_time and end_time from the transcript timestamps
6. Confidence 0.0-1.0 based on how clearly the topic was discussed
7. Classify every mentioned topic; do not omit any
"""

CLASSIFICATION_USER_PROMPT_TEMPLATE = """\
Analyze the following video transcript and classify its content.
Extract all topics discussed, summarize opinions, identify sentiment, and note key price levels.

TRANSCRIPT:
---
{transcript_text}
---

Provide the classification in the specified JSON format only.
"""

# Keyword hints for lightweight pre-classification (Phase 1 reference)
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "borsa-istanbul": ["borsa istanbul", "bist", "xu100", "hisse", "endeks"],
    "altin": ["altın", "ons altın", "gram altın", "gold"],
    "gumus": ["gümüş", "silver"],
    "dolar": ["dolar", "usd", "döviz", "kur"],
    "bitcoin-kripto": ["bitcoin", "btc", "kripto", "ethereum"],
    "dow-jones": ["dow jones", "s&p", "nasdaq", "abd borsa"],
    "petrol": ["petrol", "brent", "ham petrol"],
    "faiz": ["faiz", "mevduat", "merkez bankası"],
    "enflasyon": ["enflasyon", "tüfe", "üfe"],
    "jeopolitik": ["savaş", "iran", "abd savaş", "hürmüz", "kara harekat"],
}
