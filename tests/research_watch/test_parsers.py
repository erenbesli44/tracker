"""TCMB blog parsers against pages saved on 2026-11-09 (scripts and styles stripped)."""

from datetime import date
from pathlib import Path

from src.jobs.research_watch.parsers import parse_tcmb_article, parse_tcmb_listing

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://tcmbblog.org/wps/wcm/connect/blog/tr/main+menu/analizler/"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_listing_entries_have_absolute_url_date_category_and_authors():
    entries = parse_tcmb_listing(_read("listing.html"), BASE)
    assert len(entries) == 6
    first = entries[0]
    assert first.url == BASE + "fon_tasfiyeleri_ve_reel_sektor_firma_verileri_ne_soyluyor"
    assert first.title == "Fon Tasfiyeleri ve Reel Sektör: Firma Verileri Ne Söylüyor?"
    assert first.published_at == date(2026, 11, 4)  # <time datetime>, not the teaser's 17 Eylül
    assert first.category == "Finansal Piyasalar"
    assert first.authors == ["Kadir Gürci", "Ünal Seven"]
    assert [e.published_at.year for e in entries] == [2026, 2026, 2026, 2026, 2025, 2016]


def test_article_metadata_and_clean_body():
    article = parse_tcmb_article(_read("article_2026_11_04.html"))
    assert article.title == "Fon Tasfiyeleri ve Reel Sektör: Firma Verileri Ne Söylüyor?"
    assert article.published_at == date(2026, 11, 4)
    assert article.category == "Finansal Piyasalar"
    assert article.authors == ["Ünal Seven", "Kadir Gürci"]  # print spans, deduplicated
    body = article.body_text
    assert body.startswith("Fon piyasasındaki gelişmeler")
    assert "## Fon Yatırımlarının Büyüklüğü ve Dağılımı" in body
    assert "116 milyar TL" in body and "yüzde 1,2" in body
    assert "[4] Bu firmaların ortalama kredi maliyetleri" in body  # footnotes kept
    assert "görev yapmaktadır" not in body  # author bios stripped
    assert "Editöre Not" not in body and "EN ÇOK OKUNANLAR" not in body  # sidebar stripped
    assert "<" not in body


def test_second_article_layout_parses_the_same_way():
    article = parse_tcmb_article(_read("article_2025_12_23.html"))
    assert article.published_at == date(2025, 12, 23)
    assert article.authors == ["Ahmet Deryol", "Mehmet Emre Şamcı", "Efe Mert Ustaoğlu"]
    assert article.body_text.startswith("Türkiye Cumhuriyet Merkez Bankası")
    assert len(article.body_text) > 3000
    assert "görev yapmaktadır" not in article.body_text


def test_empty_or_foreign_pages_yield_nothing():
    assert parse_tcmb_listing("<html></html>", BASE) == []
    article = parse_tcmb_article("<html><h1>x</h1></html>")
    assert article.body_text == "" and article.published_at is None
