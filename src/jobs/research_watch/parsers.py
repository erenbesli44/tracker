"""Parsers for the TCMB blog (tcmbblog.org, IBM WebSphere Portal). Pure functions, no network.

Verified 2026-11-09 (doviz repo: docs/research/2026-11-09-tcmb-blog-explainer-review.md §2):
- The listing page holds every post as ``<article data-permalink="...">`` with
  ``<time datetime="YYYY-MM-DD">``, a category link, the title and an authors line.
- An article keeps the body in ``div.content-text``; the author boxes (``contribute`` panels)
  follow the footnotes and are not part of the text. Charts are images without captions.
- Only ``<time datetime>`` is a reliable publish date: teaser text carries other dates.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin

_ARTICLE = re.compile(r'<article\b[^>]*data-permalink="([^"]+)"[^>]*>(.*?)</article>', re.S)
_TIME = re.compile(r'<time\s+datetime="(\d{4}-\d{2}-\d{2})"')
_CATEGORY = re.compile(r'<span class="post-category">\s*<a[^>]*>(.*?)</a>', re.S)
_LIST_TITLE = re.compile(r'<p class="uk-article-title">\s*<a[^>]*>(.*?)</a>', re.S)
_LIST_AUTHORS = re.compile(r'<p class="uk-article-authors">(.*?)</p>', re.S)
_H1 = re.compile(r'<h1 class="uk-article-title">(.*?)</h1>', re.S)
_PRINT_AUTHOR = re.compile(r'<span class="author-for-print">(.*?)</span>', re.S)
_SUBTITLE = re.compile(r'<p class="uk-article-subtitle">(.*?)</p>', re.S)
_BODY_START = '<div class="content-text">'
# The first author panel ends the body; "Editöre Not" is a fallback end marker.
_BODY_END = re.compile(r'<div class="uk-panel[^"]*\bcontribute\b|Editöre Not')


@dataclass(frozen=True)
class ListingEntry:
    url: str
    title: str
    published_at: date | None
    category: str | None
    authors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Article:
    title: str
    published_at: date | None
    category: str | None
    authors: list[str]
    subtitle: str | None
    body_text: str


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _date(fragment: str) -> date | None:
    match = _TIME.search(fragment)
    return date.fromisoformat(match.group(1)) if match else None


def _first(pattern: re.Pattern, fragment: str) -> str | None:
    match = pattern.search(fragment)
    return (_text(match.group(1)) or None) if match else None


def _unique(names: list[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        if name and name not in seen:
            seen.append(name)
    return seen


def parse_tcmb_listing(page: str, base_url: str) -> list[ListingEntry]:
    """Every post on the listing page, in page order (newest first on the live site)."""
    entries: dict[str, ListingEntry] = {}
    for permalink, block in _ARTICLE.findall(page):
        url = urljoin(base_url, html.unescape(permalink))
        if url in entries:
            continue
        authors_line = _first(_LIST_AUTHORS, block) or ""
        entries[url] = ListingEntry(
            url=url,
            title=_first(_LIST_TITLE, block) or "",
            published_at=_date(block),
            category=_first(_CATEGORY, block),
            authors=_unique([a.strip() for a in authors_line.split(",")]),
        )
    return list(entries.values())


def _body_text(page: str) -> str:
    start = page.find(_BODY_START)
    if start < 0:
        return ""
    body = page[start + len(_BODY_START) :]
    end = _BODY_END.search(body)
    if end:
        body = body[: end.start()]
    body = re.sub(r"<h[2-4][^>]*>(.*?)</h[2-4]>", r"\n\n## \1\n\n", body, flags=re.S)
    body = re.sub(r"<li[^>]*>", "\n- ", body)
    body = re.sub(r"</p>|<br\s*/?>|</div>|</li>", "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", "", body)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in body.splitlines()]
    text = "\n".join(lines)
    text = re.sub(r"## \s*\n", "", text)  # headings left empty after image removal
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_tcmb_article(page: str) -> Article:
    """Title, date, authors, category and plain body text (headings as ``## ``, footnotes kept)."""
    header_end = page.find(_BODY_START)
    header = page[:header_end] if header_end > 0 else page
    return Article(
        title=_first(_H1, header) or "",
        published_at=_date(header),
        category=_first(_CATEGORY, header),
        authors=_unique([_text(a) for a in _PRINT_AUTHOR.findall(header)]),
        subtitle=_first(_SUBTITLE, header),
        body_text=_body_text(page),
    )
