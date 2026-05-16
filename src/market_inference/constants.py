"""Inference topic definitions: fixed keys mapped to existing topic slugs."""

# inference_key → list of topic.slug values in the DB
INFERENCE_TOPIC_MAP: dict[str, list[str]] = {
    "bitcoin": ["kripto-paralar"],
    "dolar-tl": ["doviz-kur"],
    "bist": ["bist-turk-piyasalari"],
    "us-markets": ["amerikan-piyasalari"],
    "altin": ["altin"],
    "gumus": ["gumus"],
    "petrol-enerji": ["petrol-enerji"],
    "faiz": ["faiz-para-politikasi"],
    "enflasyon": ["enflasyon"],
    "jeopolitik": ["jeopolitik"],
    "ic-siyaset": ["ic-siyaset"],
}

INFERENCE_TOPIC_LABELS: dict[str, str] = {
    "bitcoin": "Bitcoin / Kripto",
    "dolar-tl": "Dolar/TL Kuru",
    "bist": "BIST / Türk Borsası",
    "us-markets": "Amerikan Borsaları",
    "altin": "Altın",
    "gumus": "Gümüş",
    "petrol-enerji": "Petrol & Enerji",
    "faiz": "Faiz / Para Politikası",
    "enflasyon": "Enflasyon",
    "jeopolitik": "Jeopolitik",
    "ic-siyaset": "İç Siyaset",
}

VALID_DIRECTIONS = {"up", "down", "sideways", "mixed"}
MIN_CONFIDENCE_THRESHOLD = 0.4   # TopicMention.confidence filter
TOP_K_SOURCES = 8                 # max sources sent to LLM per topic
