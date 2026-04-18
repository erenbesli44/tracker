"""Keyword hints used by the lightweight keyword fallback classifier.

The authoritative prompt + taxonomy live in src/llm/prompts.py and
src/topics/constants.py. This module only holds the keyword-match fallback
(_auto_classification_from_transcript) that runs when the LLM call fails.
"""

# Map canonical subtopic slug -> Turkish/English keyword hints used to
# keyword-score a transcript when the LLM path is unavailable.
TOPIC_KEYWORDS: dict[str, list[str]] = {
    "bist-turk-piyasalari": [
        "borsa istanbul", "bist", "xu100", "xu030", "bist 50", "bist 100",
        "hisse", "endeks", "yerli borsa",
    ],
    "altin": ["altın", "ons altın", "gram altın", "çeyrek altın", "gold", "xau"],
    "gumus": ["gümüş", "silver", "xag"],
    "doviz-kur": [
        "dolar", "usd", "euro", "eur", "sterlin", "gbp",
        "döviz", "kur", "parite", "dxy", "eur/usd", "usd/try",
    ],
    "kripto-paralar": [
        "bitcoin", "btc", "kripto", "ethereum", "eth", "altcoin", "stablecoin",
        "usdt", "binance",
    ],
    "amerikan-piyasalari": [
        "dow jones", "s&p", "nasdaq", "wall street", "abd borsa",
        "amerikan borsa", "us futures", "magnificent",
    ],
    "petrol-enerji": [
        "petrol", "brent", "wti", "ham petrol", "doğalgaz", "lng",
        "opec", "enerji fiyat",
    ],
    "faiz-para-politikasi": [
        "faiz", "mevduat", "repo", "politika faizi", "merkez bankası",
        "tcmb", "fed", "ecb", "para politikası", "ppk", "fomc",
    ],
    "enflasyon": [
        "enflasyon", "tüfe", "üfe", "çekirdek enflasyon", "enag",
        "manşet enflasyon", "fiyat artış",
    ],
    "ic-siyaset": [
        "tbmm", "meclis", "seçim", "muhalefet", "iktidar", "kabine", "parti",
    ],
    "jeopolitik": [
        "savaş", "ateşkes", "iran", "abd savaş", "rusya", "ukrayna", "israil",
        "nato", "hürmüz", "kara harekat", "dış politika", "yaptırım",
    ],
}
