"""HttpFetcher through httpx.MockTransport: discovery, fetch, robots.txt. No network."""

from datetime import date

import httpx
import pytest
from pypdf.errors import PdfReadError

from src.jobs.broker_watch import adapters, parsers
from src.jobs.broker_watch.adapters import (
    CHANGE_NOTE,
    COMPOSITION,
    DiscoveredDocument,
    HttpFetcher,
    RobotsDisallowed,
    date_from_filename,
    pdf_to_text,
    sha256_hex,
)
from src.jobs.broker_watch.config import load_broker_config
from tests.broker_watch import fixtures as fx

UA = "dovizveri-bot/0.1 (+https://dovizveri.com/iletisim)"
BROKERS = {b.slug: b for b in load_broker_config().brokers}
TIMEOUT = {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}


class FakeSites:
    """Canned responses per URL for httpx.MockTransport; records every request.

    Unknown URLs answer 404 (so robots.txt is 'missing' unless added); URLs in
    ``failing`` raise a connection error.
    """

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes | str | list, dict[str, str]]] = {}
        self.failing: set[str] = set()
        self.requests: list[httpx.Request] = []

    def add(self, url: str, body: bytes | str | list, status: int = 200, headers=None) -> None:
        self.routes[url] = (status, body, headers or {})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url in self.failing:
            raise httpx.ConnectError("connection refused", request=request)
        if url not in self.routes:
            return httpx.Response(404, text="not found")
        status, body, headers = self.routes[url]
        if isinstance(body, list):
            return httpx.Response(status, json=body, headers=headers)
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers=headers)
        return httpx.Response(status, content=body, headers=headers)

    def fetcher(self) -> HttpFetcher:
        return HttpFetcher(UA, transport=httpx.MockTransport(self))

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _assert_bot_headers(site: FakeSites) -> None:
    assert site.requests
    for request in site.requests:
        assert request.headers["User-Agent"] == UA, request.url
        assert request.extensions["timeout"] == TIMEOUT, request.url


# ── pdf_to_text / file names ──────────────────────────────────────────────────


def test_pdf_to_text_joins_pages_with_form_feed():
    text = pdf_to_text(fx.minimal_pdf(["Eklenen Sirket SAHOL.IS", "Sayfa iki"]))
    assert text.split(parsers.PAGE_BREAK) == ["Eklenen Sirket SAHOL.IS", "Sayfa iki"]
    assert parsers.parse_isy_rss(text).text.split(parsers.PAGE_BREAK)[0] == text.split("\f")[0]


def test_pdf_to_text_is_empty_without_extractable_text_and_rejects_non_pdf():
    assert pdf_to_text(fx.minimal_pdf(["", ""])) == ""
    with pytest.raises(PdfReadError):
        pdf_to_text(b"<html><body>404 Not Found</body></html>")


@pytest.mark.parametrize(
    "name, expected",
    [
        ("20_10_2026.pdf", date(2026, 10, 20)),
        ("ModelPortfoyGuncellemesi_22_09_2026_abcd1234.pdf", date(2026, 9, 22)),
        ("modelportfoyguncellemesi_06082026.pdf", date(2026, 8, 6)),
        ("rapor_2026-09-22_12345678.pdf", None),
        ("Finansal_Degerlendirme_EREGL_20261023_3de3461d.pdf", None),
        ("31_12_1999.pdf", None),
        ("99_99_2026.pdf", None),
    ],
)
def test_date_from_filename_is_anchored(name, expected):
    assert date_from_filename(f"https://cdn.example.com/media/{name}") == expected


# ── İş Yatırım (RSS) ──────────────────────────────────────────────────────────


def test_isy_discover_follows_pages_dedupes_shared_pdf_and_skips_posts_without_pdf(caplog):
    site = FakeSites()
    site.add(fx.ISY_FEED_URL, fx.ISY_FEED_PAGE_1)
    site.add(f"{fx.ISY_FEED_URL}?paged=2", fx.ISY_FEED_PAGE_2)  # page 3 answers 404

    with site.fetcher() as fetcher:
        docs = fetcher.discover(BROKERS["is-yatirim"])

    assert [(d.kind, d.file_url, d.published_at) for d in docs] == [
        (CHANGE_NOTE, fx.ISY_PDF_20_OCT, date(2026, 10, 20)),
        (CHANGE_NOTE, fx.ISY_PDF_1_SEP, date(2026, 9, 1)),
        (CHANGE_NOTE, fx.ISY_PDF_5_FEB, date(2026, 2, 5)),
    ]
    assert docs[0].source_url.endswith("/degisiklik-47/")  # first post keeps the shared PDF
    assert site.urls() == [
        "https://arastirma.isyatirim.com.tr/robots.txt",
        fx.ISY_FEED_URL,
        f"{fx.ISY_FEED_URL}?paged=2",
        f"{fx.ISY_FEED_URL}?paged=3",
    ]
    assert "without a PDF link skipped: https://arastirma.isyatirim.com.tr/2026/08/15/duyuru/" in (
        caplog.text
    )
    _assert_bot_headers(site)


def test_isy_discover_stops_at_max_pages():
    site = FakeSites()
    site.add(fx.ISY_FEED_URL, fx.ISY_FEED_PAGE_1)
    site.add(f"{fx.ISY_FEED_URL}?paged=2", fx.ISY_FEED_PAGE_2)
    broker = BROKERS["is-yatirim"].model_copy(update={"max_pages": 1})

    with site.fetcher() as fetcher:
        docs = fetcher.discover(broker)

    assert [d.file_url for d in docs] == [fx.ISY_PDF_20_OCT, fx.ISY_PDF_1_SEP]
    assert site.urls() == ["https://arastirma.isyatirim.com.tr/robots.txt", fx.ISY_FEED_URL]


def test_isy_discover_keeps_page_1_when_page_2_is_not_a_feed(caplog):
    site = FakeSites()
    site.add(fx.ISY_FEED_URL, fx.ISY_FEED_PAGE_1)
    not_xml = "<!DOCTYPE html><html><body><p>Sayfa bulunamadı<br></body></html>"
    site.add(f"{fx.ISY_FEED_URL}?paged=2", not_xml)

    with site.fetcher() as fetcher:
        docs = fetcher.discover(BROKERS["is-yatirim"])

    assert [d.file_url for d in docs] == [fx.ISY_PDF_20_OCT, fx.ISY_PDF_1_SEP]
    assert f"{fx.ISY_FEED_URL}?paged=3" not in site.urls()
    assert "İş feed page 2 skipped (ParseError" in caplog.text


def test_fetch_refuses_files_above_the_size_cap(monkeypatch):
    site = FakeSites()
    doc = DiscoveredDocument(CHANGE_NOTE, fx.ISY_FEED_URL, fx.ISY_PDF_20_OCT, date(2026, 10, 20))
    too_big = {"Content-Length": str(adapters.MAX_PDF_BYTES + 1)}
    site.add(fx.ISY_PDF_20_OCT, b"%PDF-1.4", headers=too_big)  # declared size above the cap
    with site.fetcher() as fetcher, pytest.raises(ValueError, match="too large"):
        fetcher.fetch(BROKERS["is-yatirim"], doc)

    monkeypatch.setattr(adapters, "MAX_PDF_BYTES", 10)  # actual body above the cap
    site.add(fx.ISY_PDF_20_OCT, fx.minimal_pdf(["x"]))
    with site.fetcher() as fetcher, pytest.raises(ValueError, match="too large"):
        fetcher.fetch(BROKERS["is-yatirim"], doc)


def test_isy_fetch_downloads_pdf_and_extracts_text():
    pdf = fx.minimal_pdf(["Eklenen Sirket SAHOL.IS", "Sayfa iki"])
    site = FakeSites()
    site.add(fx.ISY_PDF_20_OCT, pdf)
    doc = DiscoveredDocument(CHANGE_NOTE, fx.ISY_FEED_URL, fx.ISY_PDF_20_OCT, date(2026, 10, 20))

    with site.fetcher() as fetcher:
        fetched = fetcher.fetch(BROKERS["is-yatirim"], doc)

    assert fetched.sha256 == sha256_hex(pdf)
    assert fetched.text == "Eklenen Sirket SAHOL.IS\fSayfa iki"
    assert site.urls() == ["https://arastirma.isyatirim.com.tr/robots.txt", fx.ISY_PDF_20_OCT]
    _assert_bot_headers(site)


# ── Yapı Kredi Yatırım (JSON) ─────────────────────────────────────────────────


def test_yk_discover_and_fetch_use_cookie_referer_and_encoded_file_path():
    site = FakeSites()
    site.add(
        fx.YK_RESEARCH_PAGE,
        "<html>arastirma</html>",
        headers={"Set-Cookie": "ASP.NET_SessionId=abc123; Path=/"},
    )
    site.add(fx.YK_REPORTS_URL, fx.YK_REPORTS_JSON)
    pdf = fx.minimal_pdf(["Model portfoyumuzden Vakifbank"])
    broker = BROKERS["yapi-kredi-yatirim"]

    with site.fetcher() as fetcher:
        docs = fetcher.discover(broker)
        assert len(docs) == 1  # not the non-public one, not the "Strateji Notu"
        doc = docs[0]
        assert (doc.kind, doc.source_url, doc.published_at) == (
            CHANGE_NOTE,
            fx.YK_RESEARCH_PAGE,
            date(2026, 9, 14),
        )
        assert doc.file_url == (
            "https://www.ykyatirim.com.tr/DetailPage/GetFilePath"
            "?url=%2FFiles%2FResearch%2F2026-09-14+Model+Portf%C3%B6y.pdf"
        )
        site.add(doc.file_url, pdf)
        fetched = fetcher.fetch(broker, doc)

    assert fetched.text == "Model portfoyumuzden Vakifbank"
    assert fetched.sha256 == sha256_hex(pdf)
    assert [(r.method, str(r.url)) for r in site.requests] == [
        ("GET", "https://www.ykyatirim.com.tr/robots.txt"),
        ("GET", fx.YK_RESEARCH_PAGE),  # sets the session cookie
        ("POST", fx.YK_REPORTS_URL),
        ("GET", doc.file_url),
    ]
    post, file_request = site.requests[2], site.requests[3]
    assert post.headers["Referer"] == fx.YK_RESEARCH_PAGE
    assert post.headers["Content-Type"] == "application/json"
    assert file_request.headers["Referer"] == fx.YK_RESEARCH_PAGE
    assert file_request.headers["Cookie"] == "ASP.NET_SessionId=abc123"
    _assert_bot_headers(site)


# ── Gedik Yatırım (page) ──────────────────────────────────────────────────────


def test_gedik_discover_lists_table_and_update_pdfs_but_skips_monthly_and_weekly():
    site = FakeSites()
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)
    pdf = fx.minimal_pdf(["Tupras (TUPRS) cikariyoruz: Sebep."])
    site.add(fx.GEDIK_PDF_22_SEP, pdf)
    broker = BROKERS["gedik-yatirim"]

    with site.fetcher() as fetcher:
        docs = fetcher.discover(broker)
        assert [(d.kind, d.file_url, d.published_at) for d in docs] == [
            (COMPOSITION, None, None),
            (CHANGE_NOTE, fx.GEDIK_PDF_22_SEP, date(2026, 9, 22)),
            (CHANGE_NOTE, fx.GEDIK_PDF_6_AUG, date(2026, 8, 6)),
        ]
        table = fetcher.fetch(broker, docs[0])
        note = fetcher.fetch(broker, docs[1])

    assert table.text == parsers.gedik_composition_table(fx.GEDIK_PAGE_HTML)
    assert table.text.startswith("Şirket Adı") and table.text.endswith("YKBNK 1₺ %1 %1")
    assert table.sha256 == sha256_hex(table.text.encode("utf-8"))
    assert note.text == "Tupras (TUPRS) cikariyoruz: Sebep." and note.sha256 == sha256_hex(pdf)
    # The page fetched during discovery is reused for the table; robots once per host.
    assert site.urls() == [
        "https://gedik.com/robots.txt",
        fx.GEDIK_PAGE_URL,
        "https://cdn.gedik.com/robots.txt",
        fx.GEDIK_PDF_22_SEP,
    ]
    _assert_bot_headers(site)


def test_fetcher_closes_its_client():
    site = FakeSites()
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)
    fetcher = site.fetcher()

    with fetcher:
        assert len(fetcher.discover(BROKERS["gedik-yatirim"])) == 3
    with pytest.raises(RuntimeError, match="closed"):
        fetcher.discover(BROKERS["gedik-yatirim"])


# ── robots.txt ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rule", ["Disallow: /media/", "Disallow: /*.pdf$", "Disallow: /media/*.pdf$"]
)
def test_robots_disallow_skips_pdfs_and_is_fetched_once_per_host(rule):
    site = FakeSites()
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)
    site.add("https://cdn.gedik.com/robots.txt", f"User-agent: *\n{rule}\n")
    site.add(fx.GEDIK_PDF_22_SEP, fx.minimal_pdf(["x"]))
    broker = BROKERS["gedik-yatirim"]

    with site.fetcher() as fetcher:
        docs = fetcher.discover(broker)
        for doc in docs[1:]:
            with pytest.raises(RobotsDisallowed, match=doc.file_url.rsplit("/", 1)[-1]):
                fetcher.fetch(broker, doc)
        fetcher.fetch(broker, docs[0])  # the page host (gedik.com) has no robots.txt

    urls = site.urls()
    assert urls.count("https://cdn.gedik.com/robots.txt") == 1
    assert urls.count("https://gedik.com/robots.txt") == 1
    assert fx.GEDIK_PDF_22_SEP not in urls and fx.GEDIK_PDF_6_AUG not in urls


def test_robots_disallow_for_our_agent_blocks_discovery():
    site = FakeSites()
    site.add(fx.ISY_FEED_URL, fx.ISY_FEED_PAGE_1)
    site.add(
        "https://arastirma.isyatirim.com.tr/robots.txt",
        "User-agent: Googlebot\nDisallow:\n\nUser-agent: dovizveri-bot\nDisallow: /\n",
    )

    with site.fetcher() as fetcher, pytest.raises(RobotsDisallowed):
        fetcher.discover(BROKERS["is-yatirim"])
    assert fx.ISY_FEED_URL not in site.urls()


def test_robots_applies_to_redirect_hops():
    site = FakeSites()
    site.add("https://cdn.gedik.com/robots.txt", "User-agent: *\nDisallow: /private/\n")
    moved = "https://cdn.gedik.com/private/ModelPortfoyGuncellemesi_22_09_2026.pdf"
    site.add(fx.GEDIK_PDF_22_SEP, "", status=302, headers={"Location": moved})
    site.add(moved, fx.minimal_pdf(["x"]))
    doc = DiscoveredDocument(CHANGE_NOTE, fx.GEDIK_PAGE_URL, fx.GEDIK_PDF_22_SEP, date(2026, 9, 22))

    with site.fetcher() as fetcher, pytest.raises(RobotsDisallowed, match="/private/"):
        fetcher.fetch(BROKERS["gedik-yatirim"], doc)

    assert site.urls() == ["https://cdn.gedik.com/robots.txt", fx.GEDIK_PDF_22_SEP]


def test_robots_missing_allows_fetching(caplog):
    site = FakeSites()  # no robots.txt route: 404
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)

    with site.fetcher() as fetcher:
        docs = fetcher.discover(BROKERS["gedik-yatirim"])

    assert docs[0].kind == COMPOSITION
    assert site.urls() == ["https://gedik.com/robots.txt", fx.GEDIK_PAGE_URL]
    assert "disallowing" not in caplog.text


@pytest.mark.parametrize("robots", ["server_error", "unreachable"])
def test_robots_server_error_or_unreachable_disallows_the_host_this_run(robots, caplog):
    site = FakeSites()
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)
    if robots == "server_error":
        site.add("https://gedik.com/robots.txt", "boom", status=500)
    else:
        site.failing.add("https://gedik.com/robots.txt")

    with site.fetcher() as fetcher:
        with pytest.raises(RobotsDisallowed):
            fetcher.discover(BROKERS["gedik-yatirim"])
        with pytest.raises(RobotsDisallowed):  # cached for the run: robots.txt not retried
            fetcher.discover(BROKERS["gedik-yatirim"])

    assert site.urls() == ["https://gedik.com/robots.txt"]
    assert "disallowing the host this run" in caplog.text
