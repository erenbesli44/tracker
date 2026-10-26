"""Live source adapters: discover and fetch documents for the three brokers.

Network only; parsing lives in ``parsers.py``. ``HttpFetcher`` is the production
``SourceFetcher``; tests drive it through ``httpx.MockTransport`` or inject a fake
with the same two methods, and never touch the network. Parsers expect PDF pages
joined with form feeds (``\\f``), which ``pdf_to_text`` produces.

robots.txt is fetched once per host and run and honoured for the configured
User-Agent (plan criterion F); a missing or unreachable robots.txt allows.
"""

import hashlib
import html
import io
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from urllib.parse import urlencode, urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx
from pypdf import PdfReader

from src.jobs.broker_watch.config import WatchedBroker
from src.jobs.broker_watch.parsers import PAGE_BREAK, gedik_composition_table

logger = logging.getLogger(__name__)

CHANGE_NOTE = "change_note"
COMPOSITION = "composition"

YK_RESEARCH_PAGE = "https://www.ykyatirim.com.tr/hizmetler/arastirma"
_RSS_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_PDF_URL = re.compile(r"https?://[^\s\"'<>]+?\.pdf\b", re.IGNORECASE)
# Date in PDF file names: 20_10_2026.pdf (İş), ModelPortfoyGuncellemesi_06_08_2026_x.pdf,
# modelportfoyguncellemesi_22092026.pdf (Gedik). Anchored on both sides so that hashes
# (_12345678.pdf) and ISO dates are not read as dd mm yyyy; years outside 2000-2100 fail.
_FILENAME_DATE = re.compile(r"(?<!\d)(\d{2})_?(\d{2})_?(20\d{2}|2100)(?!\d)")


@dataclass(frozen=True)
class DiscoveredDocument:
    kind: str  # change_note / composition
    source_url: str  # page on the broker's own domain that lists the document
    file_url: str | None  # PDF URL; None for an HTML page
    published_at: date | None


@dataclass
class FetchedDocument:
    sha256: str
    text: str  # PDF pages joined with form feeds, or the composition table text


class SourceFetcher(Protocol):
    def discover(self, broker: WatchedBroker) -> list[DiscoveredDocument]: ...

    def fetch(self, broker: WatchedBroker, doc: DiscoveredDocument) -> FetchedDocument: ...


class RobotsDisallowed(Exception):
    """The host's robots.txt forbids this URL for our User-Agent."""


def pdf_to_text(data: bytes) -> str:
    """Text of a PDF with pages joined by form feeds; '' when nothing is extractable.

    Data that is not a PDF raises pypdf's error, which the service records as a
    fetch failure for that document.
    """
    reader = PdfReader(io.BytesIO(data))
    pages = [
        (page.extract_text() or "").replace("\r\n", "\n").replace("\r", "\n")
        for page in reader.pages
    ]
    if not any(page.strip() for page in pages):
        return ""
    return PAGE_BREAK.join(pages)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def date_from_filename(url: str) -> date | None:
    m = _FILENAME_DATE.search(url.rsplit("/", 1)[-1])
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _with_query(base: str, **params: object) -> str:
    """``base`` with ``params`` URL-encoded into its query string."""
    return f"{base}{'&' if urlsplit(base).query else '?'}{urlencode(params)}"


class HttpFetcher:
    """httpx-based discovery and download with the configured User-Agent.

    Use as a context manager (or call ``close()``) so the HTTP client is released.
    """

    def __init__(
        self,
        user_agent: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._pages: dict[str, str] = {}  # HTML fetched during discovery, reused by fetch()
        self._robots: dict[str, RobotFileParser | None] = {}  # host -> rules (None: allow all)

    def __enter__(self) -> "HttpFetcher":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ── SourceFetcher ─────────────────────────────────────────────────────────

    def discover(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
        if broker.adapter == "isy_rss":
            return self._discover_isy(broker)
        if broker.adapter == "yk_json":
            return self._discover_yk(broker)
        if broker.adapter == "gedik_page":
            return self._discover_gedik(broker)
        raise ValueError(f"unknown adapter: {broker.adapter}")

    def fetch(self, broker: WatchedBroker, doc: DiscoveredDocument) -> FetchedDocument:
        if doc.kind == COMPOSITION:
            page = self._pages.get(doc.source_url) or self._get(doc.source_url).text
            table = gedik_composition_table(page)
            return FetchedDocument(sha256=sha256_hex(table.encode("utf-8")), text=table)
        if not doc.file_url:
            raise ValueError(f"{doc.kind} document without file_url: {doc.source_url}")
        # Yapı Kredi's file endpoint requires the research-page cookies (set in discover)
        # and that page as Referer.
        headers = {"Referer": YK_RESEARCH_PAGE} if broker.adapter == "yk_json" else {}
        data = self._get(doc.file_url, headers=headers).content
        return FetchedDocument(sha256=sha256_hex(data), text=pdf_to_text(data))

    # ── HTTP + robots.txt ─────────────────────────────────────────────────────

    def _get(self, url: str, **kwargs) -> httpx.Response:
        self._check_robots(url)
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.netloc not in self._robots:
            self._robots[parts.netloc] = self._load_robots(
                f"{parts.scheme}://{parts.netloc}/robots.txt"
            )
        rules = self._robots[parts.netloc]
        if rules is not None and not rules.can_fetch(self._user_agent, url):
            raise RobotsDisallowed(url)

    def _load_robots(self, robots_url: str) -> RobotFileParser | None:
        """Rules of one host; None (allow all) when the file is missing or unreachable."""
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError as exc:
            logger.warning("robots.txt unreachable, assuming allowed: %s (%s)", robots_url, exc)
            return None
        if response.status_code >= 500:
            logger.warning(
                "robots.txt returned %d, assuming allowed: %s", response.status_code, robots_url
            )
            return None
        if response.status_code >= 400:
            return None  # no robots.txt
        rules = RobotFileParser()
        rules.parse(response.text.splitlines())
        return rules

    # ── Per-source discovery ──────────────────────────────────────────────────

    def _discover_isy(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
        """RSS items -> PDF links in their bodies, following ``?paged=N`` up to
        ``broker.max_pages``. The PDF is the record: posts share a 'latest change' body,
        so documents are deduplicated by PDF URL (and later sha256)."""
        docs: dict[str, DiscoveredDocument] = {}
        for page in range(1, broker.max_pages + 1):
            url = broker.url if page == 1 else _with_query(broker.url, paged=page)
            try:
                root = ElementTree.fromstring(self._get(url).content)
            except httpx.HTTPStatusError as exc:
                if page == 1:
                    raise
                logger.info("İş feed page %d not available (%s)", page, exc.response.status_code)
                break
            items = list(root.iter("item"))
            if not items:
                break
            for item in items:
                link = item.findtext("link") or url
                body = " ".join(
                    item.findtext(tag) or "" for tag in ("description", f"{_RSS_CONTENT_NS}encoded")
                )
                pdf_urls = _PDF_URL.findall(html.unescape(body))
                if not pdf_urls:
                    logger.warning("İş post without a PDF link skipped: %s", link)
                for pdf_url in pdf_urls:
                    docs.setdefault(
                        pdf_url,
                        DiscoveredDocument(
                            kind=CHANGE_NOTE,
                            source_url=link,
                            file_url=pdf_url,
                            published_at=date_from_filename(pdf_url),
                        ),
                    )
        return list(docs.values())

    def _discover_yk(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
        """Reports JSON (POST, empty body) filtered to public 'Model Portföy' entries."""
        self._get(YK_RESEARCH_PAGE)  # sets the session cookies the file endpoint requires
        self._check_robots(broker.url)
        response = self._client.post(
            broker.url,
            content=b"",
            headers={"Content-Type": "application/json", "Referer": YK_RESEARCH_PAGE},
        )
        response.raise_for_status()
        docs: list[DiscoveredDocument] = []
        for item in response.json():
            if not (
                item.get("isPublic")
                and item.get("reportTypeName") == "Model Portföy"
                and item.get("filePath")
            ):
                continue
            posted = item.get("reportPostTime") or ""
            docs.append(
                DiscoveredDocument(
                    kind=CHANGE_NOTE,
                    source_url=YK_RESEARCH_PAGE,
                    file_url=_with_query(
                        f"{broker.site_url}/DetailPage/GetFilePath", url=item["filePath"]
                    ),
                    published_at=date.fromisoformat(posted[:10]) if posted else None,
                )
            )
        return docs

    def _discover_gedik(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
        """The page itself (composition table) plus the model-portfolio update PDFs it links."""
        page = self._get(broker.url).text
        self._pages[broker.url] = page
        docs = [
            DiscoveredDocument(
                kind=COMPOSITION, source_url=broker.url, file_url=None, published_at=None
            )
        ]
        for pdf_url in dict.fromkeys(html.unescape(u) for u in _PDF_URL.findall(page)):
            name = pdf_url.rsplit("/", 1)[-1].lower().replace("-", "").replace("_", "")
            if "modelportfoy" not in name:
                continue
            if "strateji" in name:
                continue  # monthly strategy PDF: no parse rule yet (plan, Sources table)
            docs.append(
                DiscoveredDocument(
                    kind=CHANGE_NOTE,
                    source_url=broker.url,
                    file_url=pdf_url,
                    published_at=date_from_filename(pdf_url),
                )
            )
        return docs
