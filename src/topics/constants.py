"""
Topic taxonomy seed data.
parent_slug = None means it is a main/root topic.
"""

TOPIC_SEED = [
    # ── Main topics ────────────────────────────────────────────────────────
    {"name": "Ekonomi", "slug": "ekonomi", "parent_slug": None, "description": "Ekonomi, finans, yatırım"},
    {"name": "Siyaset", "slug": "siyaset", "parent_slug": None, "description": "Siyasi gelişmeler"},
    {"name": "Spor", "slug": "spor", "parent_slug": None, "description": "Spor haberleri"},
    {"name": "Teknoloji", "slug": "teknoloji", "parent_slug": None, "description": "Teknoloji ve yenilikler"},
    # ── Ekonomi sub-topics ─────────────────────────────────────────────────
    {"name": "Borsa İstanbul", "slug": "borsa-istanbul", "parent_slug": "ekonomi", "description": "BIST, XU100, hisse senedi"},
    {"name": "Altın", "slug": "altin", "parent_slug": "ekonomi", "description": "Ons altın, gram altın, gold"},
    {"name": "Gümüş", "slug": "gumus", "parent_slug": "ekonomi", "description": "Ons gümüş, silver"},
    {"name": "Dolar", "slug": "dolar", "parent_slug": "ekonomi", "description": "USD/TRY, döviz, kur"},
    {"name": "Bitcoin/Kripto", "slug": "bitcoin-kripto", "parent_slug": "ekonomi", "description": "Bitcoin, kripto para"},
    {"name": "Dow Jones", "slug": "dow-jones", "parent_slug": "ekonomi", "description": "Dow Jones, S&P 500, yurt dışı borsalar"},
    {"name": "Petrol", "slug": "petrol", "parent_slug": "ekonomi", "description": "Brent petrol, ham petrol"},
    {"name": "Faiz", "slug": "faiz", "parent_slug": "ekonomi", "description": "Merkez Bankası faizi, mevduat"},
    {"name": "Enflasyon", "slug": "enflasyon", "parent_slug": "ekonomi", "description": "TÜFE, ÜFE, fiyat artışı"},
    # ── Siyaset sub-topics ─────────────────────────────────────────────────
    {"name": "İç Siyaset", "slug": "ic-siyaset", "parent_slug": "siyaset", "description": "Türkiye iç politikası"},
    {"name": "Dış Siyaset", "slug": "dis-siyaset", "parent_slug": "siyaset", "description": "Uluslararası ilişkiler"},
    {"name": "Jeopolitik", "slug": "jeopolitik", "parent_slug": "siyaset", "description": "Savaş, uluslararası krizler"},
]
