"""Live source adapters: discover and fetch documents for the three brokers.

Network only; parsing lives in ``parsers.py``. ``HttpFetcher`` is the production
``SourceFetcher``; tests inject a fake with the same two methods and never touch
the network.
"""

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Protocol
from xml.etree import ElementTree

import httpx

from src.jobs.broker_watch.config import WatchedBroker
from src.jobs.broker_watch.parsers import gedik_composition_table

CHANGE_NOTE = "change_note"
COMPOSITION = "composition"

YK_RESEARCH_PAGE = "https://www.ykyatirim.com.tr/hizmetler/arastirma"
_RSS_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_PDF_URL = re.compile(r"https?://[^\s\"'<>]+?\.pdf\b", re.IGNORECASE)
# Date in PDF file names: 20_10_2026.pdf (İş), ModelPortfoyGuncellemesi_06_08_2026_x.pdf,
# modelportfoyguncellemesi_22092026.pdf (Gedik).
_FILENAME_DATE = re.compile(r"(\d{2})_?(\d{2})_?(\d{4})")


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


def pdf_to_text(data: bytes) -> str:
    """Extract text from PDF bytes, pages separated by form feeds.

    Stub: no PDF library is in uv.lock yet (OI-027 step 1 open question). The
    choice of library is the owner's; the fake fetcher in tests bypasses this.
    """
    raise NotImplementedError(
        "pdf_to_text needs a PDF text library (pymupdf is not in uv.lock); see OI-027"
    )


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


class HttpFetcher:
    """httpx-based discovery and download with the configured User-Agent."""

    def __init__(
        self, user_agent: str, timeout: float = 30.0, client: httpx.Client | None = None
    ) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent}, timeout=timeout, follow_redirects=True
        )
        self._pages: dict[str, str] = {}  # HTML fetched during discovery, reused by fetch()

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

    # ── Per-source discovery ──────────────────────────────────────────────────

    def _get(self, url: str, **kwargs) -> httpx.Response:
        response = self._client.get(url, **kwargs)
        response.raise_for_status()
        return response

    def _discover_isy(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
        """RSS items -> PDF links in their bodies. The PDF is the record: posts share a
        'latest change' body, so documents are deduplicated by PDF URL (and later sha256)."""
        root = ElementTree.fromstring(self._get(broker.url).content)
        docs: dict[str, DiscoveredDocument] = {}
        for item in root.iter("item"):
            link = item.findtext("link") or broker.url
            body = " ".join(
                item.findtext(tag) or "" for tag in ("description", f"{_RSS_CONTENT_NS}encoded")
            )
            for pdf_url in _PDF_URL.findall(html.unescape(body)):
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
                    file_url=f"{broker.site_url}/DetailPage/GetFilePath?url={item['filePath']}",
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
