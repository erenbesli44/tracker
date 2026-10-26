"""Trimmed text excerpts of broker documents used by the broker_watch tests.

Each excerpt is at most 400 characters, keeps only what the parse rules need,
and is attributed to its source. Whole documents are never copied (copyright).
Table rows carry placeholder numbers, not the brokers' figures.
"""

from datetime import date

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
