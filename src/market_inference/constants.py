"""Inference topic definitions: fixed keys mapped to existing topic slugs."""

# inference_key → list of topic.slug values in the DB
INFERENCE_TOPIC_MAP: dict[str, list[str]] = {
    "bitcoin": ["kripto-paralar"],
    "altin-gr": ["altin"],
    "usd-try": ["doviz-kur"],
    "bist-100": ["bist-turk-piyasalari"],
    "nasdaq-100": ["amerikan-piyasalari"],
    "sp-500": ["amerikan-piyasalari"],
    "brent-petrol": ["petrol-enerji"],
    "gumus": ["gumus"],
    "faiz": ["faiz-para-politikasi"],
    "enflasyon": ["enflasyon"],
    "jeopolitik": ["jeopolitik"],
    "ic-siyaset": ["ic-siyaset"],
}

INFERENCE_TOPIC_LABELS: dict[str, str] = {
    "bitcoin": "Bitcoin",
    "altin-gr": "Altın gr",
    "usd-try": "USD/TRY",
    "bist-100": "BIST 100",
    "nasdaq-100": "NASDAQ 100",
    "sp-500": "S&P 500",
    "brent-petrol": "Brent petrol",
    "gumus": "Gümüş",
    "faiz": "Faiz / Para Politikası",
    "enflasyon": "Enflasyon",
    "jeopolitik": "Jeopolitik",
    "ic-siyaset": "İç Siyaset",
}

INFERENCE_TOPIC_ALIASES: dict[str, str] = {
    "altin": "altin-gr",
    "dolar-tl": "usd-try",
    "bist": "bist-100",
    "us-markets": "sp-500",
    "petrol-enerji": "brent-petrol",
}

VALID_DIRECTIONS = {"up", "down", "sideways", "mixed", "positive", "negative", "neutral"}
MIN_CONFIDENCE_THRESHOLD = 0.4   # TopicMention.confidence filter
TOP_K_SOURCES = 8                 # max sources sent to LLM per topic
