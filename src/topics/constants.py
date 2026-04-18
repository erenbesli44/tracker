"""
Topic taxonomy seed data.
parent_slug = None means it is a main/root topic.

Subtopic names and slugs are intentionally broad so one subtopic can absorb
many related terms (e.g. `amerikan-piyasalari` covers Dow/S&P/Nasdaq/Wall St).
"""

TOPIC_SEED = [
    # ── Main topics ────────────────────────────────────────────────────────
    {"name": "Ekonomi", "slug": "ekonomi", "parent_slug": None, "description": "Ekonomi, finans, yatırım"},
    {"name": "Siyaset", "slug": "siyaset", "parent_slug": None, "description": "Siyasi gelişmeler"},
    {"name": "Spor", "slug": "spor", "parent_slug": None, "description": "Spor haberleri"},
    {"name": "Teknoloji", "slug": "teknoloji", "parent_slug": None, "description": "Teknoloji ve yenilikler"},
    # ── Ekonomi sub-topics ─────────────────────────────────────────────────
    {
        "name": "BIST / Türk Piyasaları",
        "slug": "bist-turk-piyasalari",
        "parent_slug": "ekonomi",
        "description": "BIST, XU100, XU030, hisse, endeks, banka/sanayi hisseleri, yerli borsa",
    },
    {
        "name": "Altın",
        "slug": "altin",
        "parent_slug": "ekonomi",
        "description": "Ons altın, gram altın, çeyrek/yarım/tam/cumhuriyet altını, gold, XAU",
    },
    {
        "name": "Gümüş",
        "slug": "gumus",
        "parent_slug": "ekonomi",
        "description": "Ons gümüş, silver, XAG",
    },
    {
        "name": "Döviz ve Kur",
        "slug": "doviz-kur",
        "parent_slug": "ekonomi",
        "description": "USD/TRY, EUR/TRY, GBP/TRY, EUR/USD, DXY, parite, döviz kuru",
    },
    {
        "name": "Kripto Paralar",
        "slug": "kripto-paralar",
        "parent_slug": "ekonomi",
        "description": "Bitcoin/BTC, Ethereum/ETH, altcoin, stablecoin, kripto borsaları",
    },
    {
        "name": "Amerikan Piyasaları",
        "slug": "amerikan-piyasalari",
        "parent_slug": "ekonomi",
        "description": "Dow Jones, S&P 500, Nasdaq, Wall Street, ABD borsaları, US futures, Magnificent 7",
    },
    {
        "name": "Petrol ve Enerji",
        "slug": "petrol-enerji",
        "parent_slug": "ekonomi",
        "description": "Brent, WTI, ham petrol, doğalgaz, OPEC, enerji fiyatları",
    },
    {
        "name": "Faiz ve Para Politikası",
        "slug": "faiz-para-politikasi",
        "parent_slug": "ekonomi",
        "description": "TCMB/Fed/ECB faizi, mevduat, repo, politika faizi, tahvil faizleri",
    },
    {
        "name": "Enflasyon",
        "slug": "enflasyon",
        "parent_slug": "ekonomi",
        "description": "TÜFE, ÜFE, çekirdek enflasyon, ENAG, fiyat artışı, sepet",
    },
    # ── Siyaset sub-topics ─────────────────────────────────────────────────
    {
        "name": "İç Siyaset",
        "slug": "ic-siyaset",
        "parent_slug": "siyaset",
        "description": "TBMM, seçim, parti, muhalefet, iktidar, kabine, yasalar",
    },
    {
        "name": "Jeopolitik / Dış İlişkiler",
        "slug": "jeopolitik",
        "parent_slug": "siyaset",
        "description": "Savaş, ateşkes, İran/ABD/Rusya/Ukrayna/İsrail, NATO, AB, BM, dış politika",
    },
]
