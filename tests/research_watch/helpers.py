"""Shared fakes for research watch tests: no network, no LLM."""

from datetime import datetime
from pathlib import Path

from src.jobs.research_watch.config import (
    ResearchSource,
    ResearchWatchConfig,
    ResearchWatchDefaults,
)

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://tcmbblog.org/wps/wcm/connect/blog/tr/main+menu/analizler/"
POST_2026_11_04 = BASE + "fon_tasfiyeleri_ve_reel_sektor_firma_verileri_ne_soyluyor"
POST_2025_12_23 = BASE + "tcmb+faiz+indirimleri+kredi+ve+mevduat+faizlerine+yansiyor+mu"
NOW = datetime(2026, 11, 9, 8, 0)


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def config(auto_publish: bool = False, **defaults) -> ResearchWatchConfig:
    return ResearchWatchConfig(
        sources=[
            ResearchSource(
                slug="tcmb-blog",
                name="Merkezin Güncesi (TCMB blogu)",
                adapter="tcmb_blog",
                url=BASE,
                disclaimer="Yazarların görüşüdür; TCMB'nin resmi görüşü değildir.",
                auto_publish=auto_publish,
            )
        ],
        defaults=ResearchWatchDefaults(**defaults),
    )


class FakeFetcher:
    """Listing fixture at BASE; the 2025 article at its URL; the 2026 article everywhere else."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_text(self, url: str) -> str:
        self.urls.append(url)
        if url == BASE:
            return read("listing.html")
        if url == POST_2025_12_23:
            return read("article_2025_12_23.html")
        return read("article_2026_11_04.html")


GOOD_DIGEST = {
    "headline": "Tasfiye edilen fonlardaki 116 milyar TL ekonomi için ne kadar büyük?",
    "why_now": "SPK 17 Eylül 2026'da bazı fonları tasfiyeye aldı; araştırmacılar bu fonlarda "
    "parası kalan firmaları inceledi.",
    "findings": [
        "Yaklaşık 3 bin firmanın bu fonlarda 116 milyar TL'si var; 57 milyar TL'si para "
        "piyasası fonlarında.",
        "Bu firmalar reel sektör aktiflerinin %1,2'sini, istihdamın %0,6'sını oluşturuyor.",
        "Yüksek maruziyetli firmalar istihdamın ve cironun on binde 3'ü.",
    ],
    "so_what": "Doğrudan etkilenen firmaların ekonomideki payı küçük; krediye erişimleri sürüyor.",
    "not_said": "Yazarlar fon sorununun ekonomiye toplam etkisini ölçmediklerini yazıyor.",
    "terms": [{"term": "Maruziyet oranı", "meaning": "Fondaki para / dönen varlıklar."}],
    "quote": "Diğer bir ifadeyle, fon maruziyeti yüksek firmaların ekonomideki ağırlığı "
    "görece düşük.",
    "basis": "Firma bilançoları, firmalar arası ticaret ve haftalık kredi verileri.",
    "topics": ["faiz-para-politikasi", "not-a-topic"],
}


def good_digest_fn(**_kwargs) -> dict:
    return dict(GOOD_DIGEST)
