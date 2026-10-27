"""The real robots.txt of the broker hosts (tests/broker_watch/robots/, fetched 2026-10-27)
run through the real matcher for the exact paths the adapters request. No network."""

from datetime import date
from pathlib import Path

import httpx
import pytest

from src.jobs.broker_watch import adapters
from src.jobs.broker_watch.adapters import CHANGE_NOTE, DiscoveredDocument, HttpFetcher
from src.jobs.broker_watch.config import load_broker_config
from src.jobs.broker_watch.robots import RobotsRules
from tests.broker_watch import fixtures as fx

ROBOTS_DIR = Path(__file__).parent / "robots"
CONFIG = load_broker_config()
UA = CONFIG.defaults.user_agent
BROKERS = {b.slug: b for b in CONFIG.brokers}

ISY = "arastirma.isyatirim.com.tr"
YK = "www.ykyatirim.com.tr"
GEDIK = "gedik.com"
# Query-string URLs exactly as the adapters build them (adapters._with_query), so an
# encoding change there is checked against the real rules here.
ISY_FEED_PAGE_2 = adapters._with_query(BROKERS["is-yatirim"].url, paged=2)
YK_GET_FILE = f"{BROKERS['yapi-kredi-yatirim'].site_url}/DetailPage/GetFilePath"
YK_FILE_ENCODED = adapters._with_query(YK_GET_FILE, url=fx.YK_FILE_PATH)  # a filePath
YK_FILE_TOKEN = adapters._with_query(  # a token-style filePath, as served live
    YK_GET_FILE, url="L0ZpbGVzL1Jlc2VhcmNoLzIwMjYtMDktMTQtTW9kZWwtUG9ydGZveS5wZGY"
)
# cdn.gedik.com answered HTTP 400 with this Azure error body instead of a robots file.
CDN_GEDIK_400_BODY = (
    '﻿<?xml version="1.0" encoding="utf-8"?><Error><Code>OutOfRangeInput</Code>'
    "<Message>One of the request inputs is out of range.\n"
    "RequestId:081a5d3b-001e-004b-2608-661fb9000000\n"
    "Time:2026-10-27T11:43:51.2494825Z</Message></Error>"
)
CDN_GEDIK_PDF = "https://cdn.gedik.com/media/media/brnjmjkx/modelportfoyguncellemesi_22092026.pdf"


def _robots_text(host: str) -> str:
    # newline="" keeps the bytes as served (the Yapı Kredi file has CRLF line endings).
    with (ROBOTS_DIR / f"{host}.txt").open(encoding="utf-8", newline="") as f:
        return f.read()


@pytest.mark.parametrize(
    "host, url, allowed",
    [
        (ISY, BROKERS["is-yatirim"].url, True),
        (ISY, ISY_FEED_PAGE_2, True),
        (ISY, f"https://{ISY}/wp-content/uploads/2026/10/20_10_2026.pdf", True),
        (ISY, f"https://{ISY}/wp-admin/", False),
        (ISY, f"https://{ISY}/?add-to-cart=1", False),  # wildcard rule: Disallow: /*?add-to-cart=
        (YK, adapters.YK_RESEARCH_PAGE, True),
        (YK, BROKERS["yapi-kredi-yatirim"].url, True),
        (YK, YK_FILE_ENCODED, True),
        (YK, YK_FILE_TOKEN, True),
        (YK, f"https://{YK}/media/x.pdf", False),
        (YK, f"https://{YK}/getmedia/abc/x.pdf", False),
        (GEDIK, BROKERS["gedik-yatirim"].url, True),
        (GEDIK, f"https://{GEDIK}/arama?search=x", False),
    ],
)
def test_live_robots_verdict_for_adapter_urls(host, url, allowed):
    assert httpx.URL(url).host == host
    assert RobotsRules(_robots_text(host), UA).can_fetch(url) is allowed


def test_derived_urls_match_the_adapter_shapes():
    assert ISY_FEED_PAGE_2.endswith("/toppick_deg/feed/?paged=2")
    assert YK_FILE_ENCODED.endswith("?url=%2FFiles%2FResearch%2F2026-09-14+Model+Portf%C3%B6y.pdf")


def test_config_urls_are_allowed_by_their_hosts_robots():
    for slug, host in (("is-yatirim", ISY), ("yapi-kredi-yatirim", YK), ("gedik-yatirim", GEDIK)):
        url = BROKERS[slug].url
        assert httpx.URL(url).host == host
        assert RobotsRules(_robots_text(host), UA).can_fetch(url), url


def test_fixtures_are_verbatim():
    isy = _robots_text(ISY)
    assert isy.count("User-agent: *") == 2  # second group: YOAST block, empty Disallow
    assert "Disallow:\n" in isy
    yk = _robots_text(YK)
    assert "\r\n" in yk and "Disallow: /media/*.pdf$" in yk
    assert "Disallow: /arama?search" in _robots_text(GEDIK)


def test_cdn_gedik_robots_http_400_allows_the_pdf():
    requests: list[httpx.Request] = []

    def cdn(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                400, text=CDN_GEDIK_400_BODY, headers={"Content-Type": "application/xml"}
            )
        return httpx.Response(200, content=fx.minimal_pdf(["Tupras (TUPRS) cikariyoruz: x."]))

    broker = BROKERS["gedik-yatirim"]
    doc = DiscoveredDocument(CHANGE_NOTE, broker.url, CDN_GEDIK_PDF, date(2026, 9, 22))
    with HttpFetcher(UA, transport=httpx.MockTransport(cdn)) as fetcher:
        fetched = fetcher.fetch(broker, doc)

    assert fetched.text == "Tupras (TUPRS) cikariyoruz: x."
    assert [str(r.url) for r in requests] == ["https://cdn.gedik.com/robots.txt", CDN_GEDIK_PDF]
