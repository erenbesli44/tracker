"""Trimmed text excerpts of broker documents used by the broker_watch tests.

Each excerpt is at most 400 characters, keeps only what the parse rules need,
and is attributed to its source. Whole documents are never copied (copyright).
Table rows carry placeholder numbers, not the brokers' figures.
"""

from datetime import UTC, date, datetime
from email.utils import format_datetime

# İş Yatırım, "En Çok Önerilenler Listesinde Değişiklikler", 20 Oct 2026, PDF page 1,
# https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/10/20_10_2026.pdf
ISY_20_OCT_TEXT = (
    "Model Portföy Değişiklikler AKBNK.IS & SAHOL.IS: Model portföyümüzde Akbank'ı Sabancı "
    "Holding ile değiştirerek, holdingin giderek daha cazip hale gelen değerleme iskontosundan "
    "faydalanmayı hedefliyoruz. Bununla birlikte, Sabancı Holding üzerinden Akbank'a dolaylı "
    "olarak yatırım yapmaya devam ediyoruz. Eklenen Şirket SAHOL.IS Çıkartılan Şirket AKBNK.IS "
    "20.10.2026"
)
ISY_20_OCT_DATE = date(2026, 10, 20)

# İş Yatırım, weight change note, 1 Sep 2026 (feed text of the toppick_deg category),
# https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/09/01_09_2026.pdf
ISY_1_SEP_TEXT = (
    "Güçlü rafineri marjlarının etkisiyle 372 TL’lik 12 aylık hedef fiyatımızı %8 aşan "
    "Tüpraş’ın portföydeki ağırlığını %7’ye indirirken, TCMB’nin olası faiz indirim sürecinin "
    "başlıca kazananları olacak faize duyarlı hisselerin AKBNK, GARAN ve ENJSA ağırlığını "
    "artırıyoruz."
)
ISY_1_SEP_DATE = date(2026, 9, 1)
# The same sentence as pypdf (plain mode) extracts it from the PDF itself, page 1,
# https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/09/01_09_2026.pdf (read 2026-10-26):
# words are split at glyph-positioning points ('h edef', 'i ndirim', 'ağ ırlığını').
ISY_1_SEP_PDF_SENTENCE = (
    "Güçlü rafineri marjlarının etkisiyle 372 TL'lik 12 aylık h edef fiyatımızı %8 aşan "
    "Tüpraş'ın portföydeki ağırlığını %7'ye indirirken, TCMB'nin olası faiz i ndirim "
    "sürecinin başlıca kazananları olacak faize duyarlı hisselerin AKBNK, GARAN ve ENJSA "
    "ağ ırlığını artırıyoruz."
)

# İş Yatırım, multi-ticker note with per-ticker paragraphs and plural labels, 15 Jan 2026,
# PDF page 1 (pypdf plain mode, read 2026-10-27), https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/01/15_01_2026.pdf
# Stitched excerpt, not contiguous: of the four paragraphs (ENKAI, YKBNK, KCHOL, AKBNK) it
# keeps the first sentence of paragraphs 1 and 4 only, and moves the "Eklenenler" header
# (which precedes KCHOL.IS in the PDF) in front of AKBNK.IS. This exercises the stripping of
# both section headers and the first-paragraph fallback (YKBNK, KCHOL have no paragraph
# here). The label line is verbatim ("Şirketlerler" is the broker's own typo).
ISY_15_JAN_TEXT = (
    "Model Portföy Değişiklikler Çıkanlar ENKAI.IS: ENKAI hisselerini 24.03.2025 tarihli "
    "girişinden itibare n sağladığı mutlak % 42, göreceli %7 getiri sonrası model portföyün "
    "betasını artırm a amaçlı En Çok Önerilenler listemizden çıkarıyoruz. Eklenenler AKBNK.IS: "
    "Akbank ’ı model portföyümüze ekliyoruz. Çıkartılan Şirketlerler ENKAI.IS & YKBNK.IS "
    "Eklenen Şirketler KCHOL.IS & AKBNK.IS"
)
ISY_15_JAN_DATE = date(2026, 1, 15)

# İş Yatırım, 12 Mar 2026, PDF page 1 (pypdf plain mode, read 2026-10-27): the label line ends
# with a stray "&" before the next heading. Not contiguous: keeps the first sentence of the
# AKBNK and ENJSA paragraphs and drops the rest of AKBNK plus the whole EKGYO and THYAO
# paragraphs in between (so those two exercise the fallback); the label line through
# "12.03.2026" is verbatim,
# https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/03/12_03_2026-1.pdf
ISY_12_MAR_TEXT = (
    "Model Portföy Değişiklikler AKBNK.IS: Akbank’ı model portföyümüzden, portföyün yüksek beta "
    "maruziyetini azaltmak amacıyla çıkarıyoruz. ENJSA.IS: ENJSA’yı 155TL hisse hedef fiyat ve "
    "%35 artış potansiyeli ile model portföye ekliyoruz. Çıkartılan Şirketlerler AKBNK.IS & "
    "EKGYO.IS & THYAO.IS Eklenen Şirketler ENJSA.IS & En Çok Önerilenler Listesinde "
    "Değişiklikler 12.03.2026"
)
ISY_12_MAR_DATE = date(2026, 3, 12)

# Gedik Yatırım, single-change note whose only header is the dated title (no colon), 5 May 2026,
# PDF page 1 (pypdf plain mode, read 2026-10-27),
# https://cdn.gedik.com/media/media/mn4cx25o/modelportfoyguncellemesi_05052026-kcaer-in.pdf
GEDIK_5_MAY_TEXT = (
    "Kocaer Çelik’i (KCAER) ekliyoruz 05 Mayıs 2026 Model Portföy Güncellemesi Kocaer Çelik’i "
    "model portföyümüze ekliyoruz. 2Ç’deki yüksek baz etkisi kısa vadeli momentum’u sınırlasa "
    "da, 2Y’deki düşük baz daha destekleyici bir kârlılık patikasına işaret ediyor; ürün "
    "fiyatlarındaki son toparlanmanın 2Ç –3Ç finansallarına yansımasını bekliyoruz."
)

# Gedik Yatırım, "Model Portföy Güncellemesi", 22 Sep 2026, PDF page 1 (reasons trimmed),
# https://cdn.gedik.com/media/media/brnjmjkx/modelportfoyguncellemesi_22092026.pdf
GEDIK_22_SEP_TEXT = (
    "Tüpraş’ı çıkarıyor, Astor Enerji’yi ekliyoruz 22 Eylül 2026 Model Portföy Güncellemesi "
    "Tüpraş’ı (TUPRS) çıkarıyoruz: Endeksin Üzerinde Getiri olan önerimizi Endekse Paralel "
    "Getiri seviyesine indirmemizin ardından Tüpraş’ı Model Portföyümüzden çıkarıyoruz. "
    "Astor Enerji’yi (ASTOR) ekliyoruz: Astor Enerji'yi model portföyümüze ekliyoruz."
)

# Gedik Yatırım, "Model Portföy Güncellemesi", 6 Aug 2026, PDF page 1 (reasons trimmed),
# https://cdn.gedik.com/media/media/q1yd2kau/modelportfoyguncellemesi_06082026.pdf
GEDIK_6_AUG_TEXT = (
    "Anadolu Hayat’ı çıkarıyor, Rönesans Gayrimenkul’u ekliyoruz 6 Ağustos 2026 "
    "Model Portföy Güncellemesi Anadolu Hayat’ı (ANHYT) çıkarıyoruz: Hisse, 2026 "
    "tahminlerimize göre 6.7x F/K ve 2.5x PD/DD çarpanlarıyla işlem görmektedir. "
    "Rönesans Gayrimenkul’u (RGYAS) ekliyoruz: Rönesans Gayrimenkul’u model portföyümüze "
    "ekliyoruz."
)

# Gedik Yatırım, hisse model portföy page, table as seen 2026-10-26 (tickers only; the
# price, return and weight cells are placeholders), https://gedik.com/analiz/model-portfoy/hisse-model-portfoy
GEDIK_COMPOSITION_ROWS = [
    ("Aksa", "AKSA"),
    ("Astor Enerji", "ASTOR"),
    ("Doğan Holding", "DOHOL"),
    ("Garanti Bankası", "GARAN"),
    ("Mlp Sağlık Hizmetleri", "MPARK"),
    ("Rönesans Gayrimenkul", "RGYAS"),
    ("Sabancı Holding", "SAHOL"),
    ("Tab Gıda Sanayi", "TABGD"),
    ("Türk Hava Yolları", "THYAO"),
    ("Yapı ve Kredi Bankası", "YKBNK"),
]
GEDIK_COMPOSITION_HTML = (
    "<html><body><script>var x = 1;</script>"
    "<table><thead><tr><th>Şirket Adı</th><th>Hisse Kodu</th><th>Hedef Fiyat</th>"
    "<th>Potansiyel Getiri</th><th>BIST-100 Ağırlık (%)</th></tr></thead><tbody>"
    + "".join(
        f"<tr><td>{name}</td><td>{ticker}</td><td>1₺</td><td>%1</td><td>%1</td></tr>"
        for name, ticker in GEDIK_COMPOSITION_ROWS
    )
    + "</tbody></table><p>*Rapor</p></body></html>"
)

# Yapı Kredi Yatırım, "Model Portföy", 14 Sep 2026, PDF page 1 (trimmed) and page 2 table
# (tickers + entry dates only), https://www.ykyatirim.com.tr/hizmetler/arastirma
YK_14_SEP_PAGE1 = (
    "14 Eylül 2026 Model portföyümüzden Vakıfbank’ı çıkartıyoruz. "
    "Vakıfbank (VAKBN TI, 38.08TL) Öneri: AL, (Hedef Fiyat: 41.00TL) Vakıfbank’ı Model "
    "Portföyümüzden güçlü performansı ve hedef fiyatımıza oldukça yaklaşması sonrası kar "
    "realizasyonu doğrultusunda çıkartıyoruz. Vakıfbank’ı Model Portföyümüze 13 Mayıs 2026’da "
    "ekledikten sonra hisse BIST-100’e göre %19 daha olumlu performans gösterdi."
)
YK_14_SEP_PAGE2 = (
    "14 Eylül 2026 – Model Portföy Model Portföy Performansı (değişiklikler öncesi) "
    "Şirket Kod Portföy Giriş Tarihi Bim Birleşik Mağazalar BIMAS 16.01.2023 "
    "Akbank AKBNK 16.10.2025 T. Vakıflar Bankası VAKBN 13.05.2026"
)
YK_14_SEP_TEXT = YK_14_SEP_PAGE1 + "\f" + YK_14_SEP_PAGE2
YK_14_SEP_DATE = date(2026, 9, 14)

# Synthetic: a Gedik-style note naming a ticker that is not in config/bist_tickers.txt.
GEDIK_UNKNOWN_TICKER_TEXT = (
    "Örnek’i ekliyoruz 1 Ekim 2026 Model Portföy Güncellemesi "
    "Örnek Şirket’i (ORNEK) ekliyoruz: Örnek Şirket’i model portföyümüze ekliyoruz."
)


# ── Synthetic HTTP fixtures for the adapter tests (httpx.MockTransport) ───────
# Shapes follow the real sources; the contents are invented and tiny.

ISY_FEED_URL = "https://arastirma.isyatirim.com.tr/category/son-haberler/toppick_deg/feed/"
ISY_PDF_20_OCT = "https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/10/20_10_2026.pdf"
ISY_PDF_1_SEP = "https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/09/01_09_2026.pdf"
ISY_PDF_5_FEB = "https://arastirma.isyatirim.com.tr/wp-content/uploads/2026/02/05_02_2026.pdf"


def _pubdate(day: date) -> str:
    """RFC 822 pubDate as WordPress writes it, e.g. 'Tue, 20 Oct 2026 07:30:00 +0000'."""
    return format_datetime(datetime(day.year, day.month, day.day, 7, 30, tzinfo=UTC))


def _rss(items: list[tuple]) -> str:
    """WordPress-style RSS; each item is (post link, PDF URL or None[, pubDate text])."""
    body = ""
    for link, pdf, *rest in items:
        inner = f'<p>Son değişiklik</p><a href="{pdf}">PDF</a>' if pdf else "<p>Duyuru</p>"
        pubdate = f"<pubDate>{rest[0]}</pubDate>" if rest else ""
        body += (
            f"<item><title>Model Portföy Değişiklikleri</title><link>{link}</link>{pubdate}"
            f"<description>Özet</description><content:encoded><![CDATA[{inner}]]>"
            "</content:encoded></item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        f"<channel><title>İş Yatırım</title>{body}</channel></rss>"
    )


ISY_FEED_PAGE_1 = _rss(
    [
        ("https://arastirma.isyatirim.com.tr/2026/10/20/degisiklik-47/", ISY_PDF_20_OCT),
        ("https://arastirma.isyatirim.com.tr/2026/10/20/degisiklik-46/", ISY_PDF_20_OCT),
        ("https://arastirma.isyatirim.com.tr/2026/09/01/degisiklik-45/", ISY_PDF_1_SEP),
        ("https://arastirma.isyatirim.com.tr/2026/08/15/duyuru/", None),
    ]
)
ISY_FEED_PAGE_2 = _rss(
    [("https://arastirma.isyatirim.com.tr/2026/02/05/degisiklik-40/", ISY_PDF_5_FEB)]
)

# Backfill: dated feed pages, newest first; page 2 straddles 2026-01-01, page 3 is older.
ISY_PDF_15_DEC_2025 = "https://arastirma.isyatirim.com.tr/wp-content/uploads/2025/12/15_12_2025.pdf"
ISY_PDF_1_DEC_2025 = "https://arastirma.isyatirim.com.tr/wp-content/uploads/2025/12/01_12_2025.pdf"
_ISY_POST = "https://arastirma.isyatirim.com.tr/{}/degisiklik-{}/"
ISY_BACKFILL_PAGES = {
    1: _rss(
        [
            (_ISY_POST.format("2026/10/20", 47), ISY_PDF_20_OCT, _pubdate(date(2026, 10, 20))),
            (_ISY_POST.format("2026/09/01", 45), ISY_PDF_1_SEP, _pubdate(date(2026, 9, 1))),
        ]
    ),
    2: _rss(
        [
            (_ISY_POST.format("2026/02/05", 40), ISY_PDF_5_FEB, _pubdate(date(2026, 2, 5))),
            (_ISY_POST.format("2025/12/15", 39), ISY_PDF_15_DEC_2025, _pubdate(date(2025, 12, 15))),
        ]
    ),
    3: _rss(
        [(_ISY_POST.format("2025/12/01", 38), ISY_PDF_1_DEC_2025, _pubdate(date(2025, 12, 1)))]
    ),
}

YK_RESEARCH_PAGE = "https://www.ykyatirim.com.tr/hizmetler/arastirma"
YK_REPORTS_URL = "https://www.ykyatirim.com.tr/DetailPage/GetResearchReports"
YK_FILE_PATH = "/Files/Research/2026-09-14 Model Portföy.pdf"
YK_REPORTS_JSON = [
    {
        "isPublic": True,
        "reportTypeName": "Model Portföy",
        "filePath": YK_FILE_PATH,
        "reportPostTime": "2026-09-14T09:12:00",
    },
    {
        "isPublic": False,
        "reportTypeName": "Model Portföy",
        "filePath": "/Files/Research/private.pdf",
        "reportPostTime": "2026-10-01T09:00:00",
    },
    {
        "isPublic": True,
        "reportTypeName": "Strateji Notu",
        "filePath": "/Files/Research/strateji.pdf",
        "reportPostTime": "2026-10-13T09:00:00",
    },
]

# Backfill: the 14 Sep report plus an older public "Model Portföy" entry.
YK_REPORTS_JSON_BACKFILL = [
    *YK_REPORTS_JSON,
    {
        "isPublic": True,
        "reportTypeName": "Model Portföy",
        "filePath": "/Files/Research/2025-12-01 Model Portföy.pdf",
        "reportPostTime": "2025-12-01T09:00:00",
    },
]

GEDIK_PAGE_URL = "https://gedik.com/analiz/model-portfoy/hisse-model-portfoy"
GEDIK_PDF_22_SEP = (
    "https://cdn.gedik.com/media/media/brnjmjkx/ModelPortfoyGuncellemesi_22_09_2026_abcd1234.pdf"
)
GEDIK_PDF_6_AUG = "https://cdn.gedik.com/media/media/q1yd2kau/modelportfoyguncellemesi_06082026.pdf"
GEDIK_PDF_MONTHLY = "https://cdn.gedik.com/media/media/zz9/ModelPortfoyStratejiRaporu_Ekim2026.pdf"
GEDIK_PDF_WEEKLY = "https://cdn.gedik.com/media/media/zz8/Haftalik_19102026.pdf"
GEDIK_PAGE_HTML = GEDIK_COMPOSITION_HTML.replace(
    "<p>*Rapor</p>",
    "<p>*Rapor</p><ul class='marketReleases'>"
    + "".join(
        f'<li><a href="{url}">{url.rsplit("/", 1)[-1]}</a></li>'
        for url in (GEDIK_PDF_22_SEP, GEDIK_PDF_6_AUG, GEDIK_PDF_MONTHLY, GEDIK_PDF_WEEKLY)
    )
    + "</ul>",
)
# Backfill: the same page with one update PDF dated before 2026.
GEDIK_PDF_15_DEC_2025 = (
    "https://cdn.gedik.com/media/media/zz6/modelportfoyguncellemesi_15122025.pdf"
)
GEDIK_PAGE_HTML_BACKFILL = GEDIK_PAGE_HTML.replace(
    "</ul>", f'<li><a href="{GEDIK_PDF_15_DEC_2025}">eski</a></li></ul>'
)


def minimal_pdf(pages: list[str]) -> bytes:
    """A valid one-font PDF with one line of ASCII text per page, for pypdf tests."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # pages dictionary, filled below
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for line in pages:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % (len(objects) + 2)
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content))
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_at,
    )
    return bytes(out)
