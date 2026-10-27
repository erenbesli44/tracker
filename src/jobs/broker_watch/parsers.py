"""Pure-text parsers for broker model-portfolio documents (no network, no LLM).

One ``parse_<adapter>`` per source. Input is the extracted text of a document
(PDF pages joined with form feeds, or the HTML of a page); output is a
``ParsedDocument`` whose quotes are verbatim substrings of ``ParsedDocument.text``
(the whitespace-normalised input). Rules follow the pilot plan in the doviz repo
(docs/research/2026-10-26-model-portfolio-pilot-plan.md): deterministic regexes,
nothing guessed. What a rule cannot map is reported in ``warnings`` or flagged
``needs_review`` -- never dropped silently.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path

from src.config import settings

MAX_QUOTE_CHARS = 300
PAGE_BREAK = "\f"

ADDED = "added"
REMOVED = "removed"
WEIGHT_UP = "weight_up"
WEIGHT_DOWN = "weight_down"

TURKISH_MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}  # fmt: skip

# İş Yatırım weight notes name some companies without a ticker. Only the cases
# seen in the fixtures are mapped; anything else is skipped with a warning.
COMPANY_TICKERS = {"Tüpraş": "TUPRS", "Akbank": "AKBNK", "Sabancı Holding": "SAHOL"}


@dataclass
class ParsedEvent:
    ticker: str
    action: str  # added / removed / weight_up / weight_down
    event_date: date | None
    reason_quote: str  # verbatim substring of ParsedDocument.text, <= 300 chars
    quote_page: int | None = None
    recommendation: str | None = None
    target_price: float | None = None
    needs_review: bool = False


@dataclass
class ParsedHolding:
    ticker: str
    entry_date: date | None = None


@dataclass
class ParsedDocument:
    text: str
    events: list[ParsedEvent] = field(default_factory=list)
    composition: list[ParsedHolding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # True when a rule saw a change it could not attribute (e.g. a weight clause
    # without a known ticker); the document must then be reviewed as a whole.
    needs_review: bool = False
    # What ``composition`` describes: "current" holdings, or "pre_change" (the list
    # before the changes announced in the same document, as in Yapı Kredi's page 2).
    composition_basis: str = "current"
    # Date printed in the document (Yapı Kredi: first Turkish date on page 1); the
    # snapshot's ``as_of`` when the source gives no publication date.
    document_date: date | None = None


# ── Ticker list ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def known_tickers() -> frozenset[str]:
    """Tickers listed in config/bist_tickers.txt (one per line, '#' comments)."""
    path = Path(settings.BIST_TICKERS_PATH)
    if not path.exists():
        return frozenset()
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(
        line.strip().upper() for line in lines if line.strip() and not line.lstrip().startswith("#")
    )


def is_known_ticker(ticker: str) -> bool:
    return ticker.upper() in known_tickers()


# ── Text helpers ──────────────────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """Collapse whitespace runs to one space; keep form feeds as page breaks."""
    text = re.sub(r"[^\S\f]+", " ", text)
    text = re.sub(r" ?\f ?", PAGE_BREAK, text)
    return text.strip()


def html_to_text(markup: str) -> str:
    """Visible text of an HTML page, whitespace-normalised (stdlib only)."""
    markup = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<[^>]+>", " ", markup)
    return normalize_text(html.unescape(markup))


def _loose(phrase: str) -> str:
    """Regex for ``phrase`` allowing an optional space in every gap between letters.

    pypdf's plain extraction splits words at glyph-positioning boundaries in some
    PDFs ('ağ ırlığını', 'h edef' in İş Yatırım's 1 Sep 2026 note); headings, body
    markers, weight nouns and company names must still match, and quotes stay
    verbatim from the stored text. Not used for the weight verb stems: loosened
    stems match across word boundaries ('az altın' -> azalt, 'in dirençli' ->
    indir) and would invent a direction. A split verb stem therefore leaves its
    clause without a verb: when no later weight clause follows in the sentence the
    clause yields no event (the document is stored for review with "no portfolio
    change found"); otherwise it is merged into the next clause's scope, where
    ``_isy_weight_changes`` sees two weight nouns and flags that scope's events and
    the document for review instead of trusting the inherited direction. That count
    uses a loose noun stem ('ağırlı', 'ağrılı': ağırlığını, ağırlıklarımızı, ağırlık,
    ...) so an unlisted noun form cannot hide a merged clause; synonyms such as
    'payını' are not counted (known residual).
    """
    return " ".join(" ?".join(re.escape(char) for char in word) for word in phrase.split())


def page_of(text: str, position: int) -> int:
    """1-based page number of ``position`` in a form-feed separated text."""
    return text.count(PAGE_BREAK, 0, max(position, 0)) + 1


_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9“\"(])")
_SENTENCE_TAIL = re.compile(r"[.!?][”\")]?$")


def build_quote(body: str, limit: int = MAX_QUOTE_CHARS) -> str:
    """Leading whole sentences of ``body`` that fit in ``limit`` chars, verbatim.

    A fragment without any sentence end (usually the next section's heading)
    yields '' so the caller flags the event instead of quoting it. If the first
    sentence alone is too long, the cut falls on the last word boundary before
    ``limit``.
    """
    body = body.strip()
    ends = [m.start() for m in _SENTENCE_BREAK.finditer(body)]
    if _SENTENCE_TAIL.search(body):
        ends.append(len(body))
    cut = 0
    for end in ends:
        if end > limit:
            break
        cut = end
    if cut == 0:
        if not ends:
            return ""
        cut = body.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
    return body[:cut].rstrip()


_DOTTED_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TURKISH_MONTH_NAMES = "Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık"
_TURKISH_DATE_RE = r"\d{1,2}\s+(?:" + _TURKISH_MONTH_NAMES + r")\s+\d{4}"
_TURKISH_DATE = re.compile(r"\b(\d{1,2})\s+(" + _TURKISH_MONTH_NAMES + r")\s+(\d{4})\b")


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def first_dotted_date(text: str) -> date | None:
    """First ``dd.mm.yyyy`` in ``text``."""
    m = _DOTTED_DATE.search(text)
    return _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def first_turkish_date(text: str) -> date | None:
    """First ``d <Turkish month name> yyyy`` in ``text``."""
    m = _TURKISH_DATE.search(text)
    if not m:
        return None
    return _safe_date(int(m.group(3)), TURKISH_MONTHS[m.group(2).lower()], int(m.group(1)))


def _fold(word: str) -> str:
    """Lowercase a Turkish word safely (str.lower() turns İ into a two-code-point i̇)."""
    return word.replace("İ", "i").replace("I", "ı").lower()


def _to_float(value: str) -> float | None:
    """'41.00' -> 41.0, '41,00' -> 41.0, '1.234,50' -> 1234.5."""
    if "," in value and "." in value:
        value = value.replace(".", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def _add_event(
    doc: ParsedDocument,
    ticker: str,
    action: str,
    event_date: date | None,
    quote: str,
    review_reason: str | None = None,
) -> ParsedEvent:
    """Append an event; flag it for review when it could not be fully validated
    (``review_reason``: a doubt the caller already found, e.g. an unverified direction)."""
    reasons = [review_reason] if review_reason else []
    if not is_known_ticker(ticker):
        reasons.append("unknown ticker")
    if event_date is None:
        reasons.append("no event date")
    if not quote:
        reasons.append("no reason quote")
    event = ParsedEvent(
        ticker=ticker,
        action=action,
        event_date=event_date,
        reason_quote=quote,
        quote_page=page_of(doc.text, doc.text.find(quote)) if quote else None,
        needs_review=bool(reasons),
    )
    if reasons:
        doc.warnings.append(f"{ticker} {action}: needs review ({', '.join(reasons)})")
    doc.events.append(event)
    return event


# ── İş Yatırım ────────────────────────────────────────────────────────────────

_ISY_HEADING = re.compile(_loose("Model Portföy Değişiklikler"))
_ISY_TICKER = re.compile(r"([A-Z0-9]{4,5})\.IS")
# One or more tickers: "SAHOL.IS", "ENKAI.IS & YKBNK.IS", "X.IS, Y.IS ve Z.IS"; a trailing
# joiner is tolerated ("Eklenen Şirketler ENJSA.IS & En Çok Önerilenler", 12 Mar 2026).
_ISY_TICKER_LIST = r"(?:[A-Z0-9]{4,5}\.IS\s*(?:&|,|ve)?\s*)+"


def _isy_label(words: str) -> str:
    """``_loose`` pattern of a list label in the singular ('Eklenen Şirket'), the plural
    ('Eklenen Şirketler') or the broker's doubled plural ('Çıkartılan Şirketlerler', as
    printed in every multi-ticker note of 2026)."""
    return _loose(words) + r"(?: ?l ?e ?r){0,2}"


_ISY_ADDED = re.compile(_isy_label("Eklenen Şirket") + r"\s+(" + _ISY_TICKER_LIST + ")")
_ISY_REMOVED = re.compile(_isy_label("Çıkartılan Şirket") + r"\s+(" + _ISY_TICKER_LIST + ")")
_ISY_BODY_END = re.compile(
    r"\s(?:"
    + "|".join(
        (
            _isy_label("Eklenen Şirket"),
            _isy_label("Çıkartılan Şirket"),
            _loose("En Çok Önerilenler Listesinde"),
        )
    )
    + r")\b"
)
# Paragraph label: "AKBNK.IS & SAHOL.IS: …" (one paragraph for both tickers), or with its
# section header, "Çıkanlar ENKAI.IS: …" / "Eklenenler KCHOL.IS: …" (15 Jan 2026).
_ISY_TICKER_LABEL = re.compile(
    r"(?<!\S)(?:(?:"
    + _loose("Çıkanlar")
    + "|"
    + _loose("Eklenenler")
    + r")\s+)?("
    + _ISY_TICKER_LIST
    + r"):\s*"
)
# Weight sentences: "<X> ağırlığını %7'ye indirirken", "... ağırlıklarını artırıyoruz";
# the verb stem decides the direction. "ağrılığını" is the broker's own typo.
_WEIGHT_NOUN_FORMS = ("ağırlığını", "ağrılığını", "ağırlıklarını")
_WEIGHT_NOUN = "(?:" + "|".join(_loose(n) for n in _WEIGHT_NOUN_FORMS) + ")"
# The clause-merge guard counts every weight-noun form (ağırlığını, ağırlıklarımızı,
# ağırlık oranı, ...) by stem, so a merged clause with an unlisted form is still seen.
_WEIGHT_NOUN_ANY_RE = re.compile("(?:" + _loose("ağırlı") + "|" + _loose("ağrılı") + ")")
# Verb stems stay strict (no _loose): see its docstring.
_WEIGHT_CLAUSE = re.compile(
    _WEIGHT_NOUN + r"\s[^;.]{0,60}?\b(indir|azalt|düşür|çek|artır|arttır|yükselt)"
)
_WEIGHT_DOWN_STEMS = ("indir", "azalt", "düşür", "çek")
# A company name counts only in the genitive right before the weight noun
# ("Tüpraş’ın portföydeki ağırlığını"); other cases ("Akbank'a", "Akbank'tan") do not.
_GENITIVE_BEFORE_WEIGHT = r"[’']n?[ıiuü]n\s+(?:\S+\s+){0,2}" + _WEIGHT_NOUN
_COMPANY_WEIGHT = {
    ticker: re.compile(_loose(name) + _GENITIVE_BEFORE_WEIGHT)
    for name, ticker in COMPANY_TICKERS.items()
}
_UPPER_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{3,4}\b")
# Any genitive after an apostrophe ("Garanti Bankası’nın", "GYO’nun"): one per company named.
_GENITIVE = re.compile(r"[’']n?[ıiuü]n\b")


def parse_isy_rss(text: str, *, published_at: date | None = None) -> ParsedDocument:
    """İş Yatırım change note (PDF page 1).

    ``Eklenen Şirket(ler) X.IS [& Y.IS]`` -> added, ``Çıkartılan Şirket(ler) X.IS [& Y.IS]``
    -> removed, weight sentences -> weight_up / weight_down. Reason of a listed ticker =
    its own paragraph (``X.IS: …``) when the note has one; else the first paragraph of the
    note, flagged for review when other tickers do have paragraphs (a shared reason
    otherwise); weight events quote the first paragraph.
    Event date = ``published_at`` (PDF filename date), else the dotted date in the text.
    """
    text = normalize_text(text)
    doc = ParsedDocument(text=text)
    page1 = text.split(PAGE_BREAK, 1)[0]
    event_date = published_at or first_dotted_date(page1)

    heading = _ISY_HEADING.search(page1)
    body = page1[heading.end() :] if heading else page1
    body = _ISY_BODY_END.split(body, maxsplit=1)[0].strip()
    paragraphs = _isy_paragraphs(body)
    first_label = _ISY_TICKER_LABEL.match(body)
    body = body[first_label.end() :] if first_label else body
    next_label = _ISY_TICKER_LABEL.search(body)
    quote = build_quote(body[: next_label.start()] if next_label else body)  # first paragraph

    for pattern, action in ((_ISY_ADDED, ADDED), (_ISY_REMOVED, REMOVED)):
        for m in pattern.finditer(page1):
            for ticker in _ISY_TICKER.findall(m.group(1)):
                if ticker in paragraphs:
                    ticker_quote = build_quote(paragraphs[ticker])
                    review_reason = None
                elif paragraphs:
                    # The note gives each ticker its own paragraph but not this one: the
                    # first paragraph is another company's reason, so it is not trusted.
                    doc.warnings.append(f"no per-ticker paragraph for {ticker}")
                    ticker_quote, review_reason = quote, "no per-ticker paragraph"
                else:
                    ticker_quote, review_reason = quote, None  # one shared reason paragraph
                _add_event(doc, ticker, action, event_date, ticker_quote, review_reason)
    for ticker, action, review_reason in _isy_weight_changes(body, doc):
        _add_event(doc, ticker, action, event_date, quote, review_reason)

    if not doc.events:
        doc.warnings.append("no portfolio change found on page 1")
    return doc


def _isy_paragraphs(body: str) -> dict[str, str]:
    """Each ticker's own paragraph: the text after its ``X.IS:`` label (``X.IS & Y.IS:``
    serves both) up to the next label; ``body`` is already cut before the list labels."""
    labels = list(_ISY_TICKER_LABEL.finditer(body))
    paragraphs: dict[str, str] = {}
    for i, m in enumerate(labels):
        end = labels[i + 1].start() if i + 1 < len(labels) else len(body)
        for ticker in _ISY_TICKER.findall(m.group(1)):
            paragraphs.setdefault(ticker, body[m.end() : end])
    return paragraphs


def _isy_weight_changes(body: str, doc: ParsedDocument) -> list[tuple[str, str, str | None]]:
    """(ticker, action, review_reason) from weight sentences; each clause scopes its own
    tickers, from the end of the previous matched clause to its own verb.

    A clause that names no known ticker, or more companies (genitive forms) than
    tickers it could map, marks the whole document for review. A scope holding more
    than one weight noun means an earlier clause's verb was not recognised (an
    unlisted verb, or a stem pypdf split such as 'i ndirirken') and that clause fell
    into this one: its tickers would inherit this verb's direction, so every event
    of the scope is returned flagged and the document goes to review. Nouns are
    counted by stem (any 'ağırlı…' form); a synonym such as 'payını' would not be
    counted, which is a known residual of this guard.
    """
    changes: list[tuple[str, str, str | None]] = []
    for sentence in _SENTENCE_BREAK.split(body):
        start = 0
        for m in _WEIGHT_CLAUSE.finditer(sentence):
            scope = sentence[start : m.end()]
            start = m.end()
            action = WEIGHT_DOWN if m.group(1) in _WEIGHT_DOWN_STEMS else WEIGHT_UP
            tickers = _tickers_in(scope, doc.warnings)
            review_reason = None
            if len(_WEIGHT_NOUN_ANY_RE.findall(scope)) > 1:
                review_reason = "weight clause without a recognised verb"
                doc.warnings.append(f"{review_reason}: {scope[:80]!r}")
                doc.needs_review = True
            if not tickers:
                doc.warnings.append(f"weight clause without a known ticker: {scope[:80]!r}")
                doc.needs_review = True
            elif len(_GENITIVE.findall(scope)) > len(tickers):
                doc.warnings.append(f"weight clause names an unmapped company: {scope[:80]!r}")
                doc.needs_review = True
            changes.extend((ticker, action, review_reason) for ticker in tickers)
    return changes


def _tickers_in(scope: str, warnings: list[str]) -> list[str]:
    """Known tickers (uppercase tokens, or mapped company names in the genitive right
    before the weight noun) in order of appearance."""
    found: list[tuple[int, str]] = []
    for m in _UPPER_TOKEN.finditer(scope):
        if is_known_ticker(m.group(0)):
            found.append((m.start(), m.group(0)))
        else:
            warnings.append(f"skipped unknown ticker candidate {m.group(0)}")
    for ticker, pattern in _COMPANY_WEIGHT.items():
        m = pattern.search(scope)
        if m:
            found.append((m.start(), ticker))
    ordered: list[str] = []
    for _, ticker in sorted(found):
        if ticker not in ordered:
            ordered.append(ticker)
    return ordered


# ── Yapı Kredi Yatırım ────────────────────────────────────────────────────────

# "Model portföyümüzden Vakıfbank’ı çıkartıyoruz" / "MODEL PORTFÖYÜMÜZE BİM'İ EKLİYORUZ".
# No re.IGNORECASE: it mishandles Turkish İ/ı; the verb is classified via _fold().
_YK_HEADER = re.compile(
    r"(?:Model [Pp]ortföyümüz|MODEL PORTFÖYÜMÜZ)(?:e|den|E|DEN)\s+([^.’']{1,40}?)[’']"
    r"(?:[nyNY]?[ıiuüIİUÜ])?\s+(\w+)"
)
_YK_TICKER = re.compile(r"\(([A-Z0-9]{4,5})\s+TI\b")
_YK_RECOMMENDATION = re.compile(r"Öneri:\s*(AL|TUT|SAT)\b")
_YK_TARGET = re.compile(r"Hedef Fiyat:\s*([\d.,]+)\s*TL\)?")
_YK_HOLDING = re.compile(r"\b([A-Z0-9]{4,5})\s+(\d{2})\.(\d{2})\.(\d{4})\b")
# Title of the page-2 table: the composition *before* the changes announced on page 1.
_YK_PRE_CHANGE_TITLE = "değişiklikler öncesi"


def parse_yk_json(text: str, *, published_at: date | None = None) -> ParsedDocument:
    """Yapı Kredi 'Model Portföy' PDF: change note on page 1, composition table on page 2.

    Event date = ``published_at`` (report post time), else the Turkish date in the text.
    Holdings are read from page 2 onwards only (known tickers), with basis
    ``pre_change`` when the table is titled "(değişiklikler öncesi)".
    """
    text = normalize_text(text)
    doc = ParsedDocument(text=text)
    page1 = text.split(PAGE_BREAK, 1)[0]
    doc.document_date = first_turkish_date(page1)
    event_date = published_at or doc.document_date

    headers = list(_YK_HEADER.finditer(page1))
    for i, m in enumerate(headers):
        verb = _fold(m.group(2))
        if verb.startswith("ekl"):
            action = ADDED
        elif verb.startswith("çık"):
            action = REMOVED
        else:
            doc.warnings.append(f"unrecognised verb in header {m.group(0)!r}")
            doc.needs_review = True
            continue
        end = headers[i + 1].start() if i + 1 < len(headers) else len(page1)
        segment = page1[m.end() : end]
        ticker_m = _YK_TICKER.search(segment)
        if not ticker_m:
            doc.warnings.append(f"no ticker after header {m.group(0)!r}")
            doc.needs_review = True
            continue
        recommendation = _YK_RECOMMENDATION.search(segment)
        target = _YK_TARGET.search(segment)
        body_start = max(x.end() for x in (ticker_m, recommendation, target) if x)
        quote = build_quote(segment[body_start:])
        event = _add_event(doc, ticker_m.group(1), action, event_date, quote)
        event.recommendation = recommendation.group(1) if recommendation else None
        event.target_price = _to_float(target.group(1)) if target else None

    tables = text.split(PAGE_BREAK, 1)[1] if PAGE_BREAK in text else ""
    seen: set[str] = set()
    for m in _YK_HOLDING.finditer(tables):
        ticker = m.group(1)
        if ticker in seen:
            continue
        seen.add(ticker)
        if not is_known_ticker(ticker):
            doc.warnings.append(f"skipped unknown holding {ticker}")
            continue
        entry = _safe_date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
        doc.composition.append(ParsedHolding(ticker=ticker, entry_date=entry))
    if _YK_PRE_CHANGE_TITLE in tables:
        doc.composition_basis = "pre_change"
    elif doc.composition:
        doc.warnings.append(
            f"composition table without the '({_YK_PRE_CHANGE_TITLE})' title: basis current"
        )

    if not headers:
        doc.warnings.append("no 'Model portföyümüze/den ...' header found on page 1")
    return doc


# ── Gedik Yatırım ─────────────────────────────────────────────────────────────

# "<Name> (<TICKER>) çıkarıyoruz: <reason>" in the body, or the dated title of a
# single-change note, "<Name> (<TICKER>) ekliyoruz 05 Mayıs 2026 Model Portföy Güncellemesi
# <reason>" (5 and 7 May 2026): the title belongs to the header, so the quote starts at
# the reason either way.
_GEDIK_HEADER = re.compile(
    r"\(([A-Z0-9]{3,6})\)\s*("
    + _loose("çıkarıyoruz")
    + "|"
    + _loose("ekliyoruz")
    + r")(?:\s*:|\s+"
    + _TURKISH_DATE_RE
    + r"\b(?:\s+"
    + _loose("Model Portföy Güncellemesi")
    + r")?)"
)
_GEDIK_TABLE_HEAD = re.compile(
    r"Şirket Adı\s+Hisse Kodu\s+Hedef Fiyat\s+Potansiyel Getiri\s+BIST-100 Ağırlık \(%\)"
)
_GEDIK_TABLE_ROW = re.compile(r"\b([A-Z0-9]{4,5})\s+[\d.,]+\s*₺\s+%-?[\d.,]+\s+%-?[\d.,]+")


def parse_gedik_page(text: str, *, published_at: date | None = None) -> ParsedDocument:
    """Gedik 'Model Portföy Güncellemesi' PDF (page 1).

    ``<Name> (<TICKER>) çıkarıyoruz: <reason>`` -> removed, ``... ekliyoruz: ...`` -> added;
    the colon may also be a dated title (``... ekliyoruz 05 Mayıs 2026 Model Portföy
    Güncellemesi``). Event date = the Turkish date in the title, else ``published_at``.
    """
    text = normalize_text(text)
    doc = ParsedDocument(text=text)
    page1 = text.split(PAGE_BREAK, 1)[0]
    event_date = first_turkish_date(page1) or published_at

    headers = list(_GEDIK_HEADER.finditer(page1))
    for i, m in enumerate(headers):
        # The next header's company name trails this body; build_quote drops that fragment.
        end = headers[i + 1].start() if i + 1 < len(headers) else len(page1)
        action = REMOVED if m.group(2).replace(" ", "") == "çıkarıyoruz" else ADDED
        _add_event(doc, m.group(1), action, event_date, build_quote(page1[m.end() : end]))

    if not headers:
        doc.warnings.append(
            "no '(TICKER) çıkarıyoruz/ekliyoruz' header (colon or dated title) found on page 1"
        )
    return doc


def gedik_composition_table(markup: str) -> str:
    """The model-portfolio table of the Gedik page as plain text ('' when absent).

    Used both to parse holdings and as the document text/identity, so a page
    re-render without a portfolio change yields the same sha256.
    """
    text = html_to_text(markup)
    head = _GEDIK_TABLE_HEAD.search(text)
    if not head:
        return ""
    end = head.end()
    for m in _GEDIK_TABLE_ROW.finditer(text, head.end()):
        if m.start() - end > 80:  # rows are contiguous: only a company name sits between
            break
        end = m.end()
    return text[head.start() : end]


def parse_gedik_composition(markup: str) -> list[ParsedHolding]:
    """Tickers of the Gedik model-portfolio HTML table, in table order (no targets)."""
    holdings: list[ParsedHolding] = []
    for m in _GEDIK_TABLE_ROW.finditer(gedik_composition_table(markup)):
        if m.group(1) not in [h.ticker for h in holdings]:
            holdings.append(ParsedHolding(ticker=m.group(1)))
    return holdings
