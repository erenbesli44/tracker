"""run_once with a fake fetcher: no network, idempotent, review flags preserved."""

import json
from datetime import date

from sqlalchemy import func
from sqlmodel import Session, select

from src.jobs.broker_watch import service
from src.jobs.broker_watch.adapters import (
    CHANGE_NOTE,
    COMPOSITION,
    DiscoveredDocument,
    FetchedDocument,
    sha256_hex,
)
from src.jobs.broker_watch.config import WatchedBroker, load_broker_config
from src.jobs.broker_watch.models import (
    Broker,
    BrokerDocument,
    BrokerWatchRun,
    BrokerWatchSourceResult,
    ModelPortfolioEvent,
    ModelPortfolioSnapshot,
)
from tests.broker_watch import fixtures as fx


def _doc(kind: str, url: str, published_at: date | None = None) -> DiscoveredDocument:
    file_url = url if kind == CHANGE_NOTE else None
    return DiscoveredDocument(
        kind=kind, source_url=url, file_url=file_url, published_at=published_at
    )


ISY_URL = "https://arastirma.isyatirim.com.tr/x"
ISY_20_OCT = _doc(CHANGE_NOTE, f"{ISY_URL}/20_10_2026.pdf", fx.ISY_20_OCT_DATE)
ISY_1_SEP = _doc(CHANGE_NOTE, f"{ISY_URL}/01_09_2026.pdf", fx.ISY_1_SEP_DATE)
YK_14_SEP = _doc(
    CHANGE_NOTE, "https://www.ykyatirim.com.tr/DetailPage/GetFilePath?url=a", fx.YK_14_SEP_DATE
)
GEDIK_PAGE = _doc(COMPOSITION, "https://gedik.com/analiz/model-portfoy/hisse-model-portfoy")
GEDIK_22_SEP = _doc(CHANGE_NOTE, "https://cdn.gedik.com/x/modelportfoyguncellemesi_22092026.pdf")

TEXTS = {
    ISY_20_OCT: fx.ISY_20_OCT_TEXT,
    ISY_1_SEP: fx.ISY_1_SEP_TEXT,
    YK_14_SEP: fx.YK_14_SEP_TEXT,
    GEDIK_PAGE: fx.GEDIK_COMPOSITION_HTML,
    GEDIK_22_SEP: fx.GEDIK_22_SEP_TEXT,
}


class FakeFetcher:
    """In-memory stand-in for adapters.HttpFetcher; never touches the network."""

    def __init__(self, docs: dict[str, list[DiscoveredDocument]], texts=TEXTS) -> None:
        self.docs = docs
        self.texts = texts
        self.fetched: list[str] = []

    def discover(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
        return self.docs.get(broker.slug, [])

    def fetch(self, broker: WatchedBroker, doc: DiscoveredDocument) -> FetchedDocument:
        self.fetched.append(doc.file_url or doc.source_url)
        text = self.texts[doc]
        return FetchedDocument(sha256=sha256_hex(text.encode("utf-8")), text=text)


def _default_docs() -> dict[str, list[DiscoveredDocument]]:
    return {
        "is-yatirim": [ISY_20_OCT, ISY_1_SEP],
        "yapi-kredi-yatirim": [YK_14_SEP],
        "gedik-yatirim": [GEDIK_PAGE, GEDIK_22_SEP],
    }


def _count(session: Session, model) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


def test_run_once_stores_documents_events_snapshots_and_is_idempotent(session: Session):
    engine = session.get_bind()
    config = load_broker_config()
    fetcher = FakeFetcher(_default_docs())

    first = service.run_once(engine, config, fetcher=fetcher)

    assert first.status == "success"
    assert first.sources_scanned == 3 and first.errors_count == 0
    assert first.documents_new == 5
    assert first.events_new == 2 + 4 + 1 + 2
    assert first.snapshots_new == 2  # Gedik HTML table + Yapı Kredi page 2
    assert _count(session, Broker) == 3
    assert _count(session, BrokerDocument) == 5
    assert _count(session, ModelPortfolioEvent) == 9
    assert _count(session, ModelPortfolioSnapshot) == 2

    second = service.run_once(engine, config, fetcher=fetcher)

    assert second.status == "success"
    assert (second.documents_new, second.events_new, second.snapshots_new) == (0, 0, 0)
    assert _count(session, BrokerDocument) == 5
    assert _count(session, ModelPortfolioEvent) == 9
    assert _count(session, ModelPortfolioSnapshot) == 2
    assert _count(session, BrokerWatchRun) == 2
    assert _count(session, BrokerWatchSourceResult) == 6
    # Known PDFs are not downloaded again; the HTML composition page is re-read every run.
    assert fetcher.fetched.count(ISY_20_OCT.file_url) == 1
    assert fetcher.fetched.count(GEDIK_PAGE.source_url) == 2


def test_stored_events_match_parsed_documents(session: Session):
    service.run_once(session.get_bind(), load_broker_config(), fetcher=FakeFetcher(_default_docs()))

    brokers = {b.slug: b for b in session.exec(select(Broker)).all()}
    events = session.exec(select(ModelPortfolioEvent)).all()
    documents = {d.id: d for d in session.exec(select(BrokerDocument)).all()}

    for event in events:
        assert len(event.reason_quote) <= 300
        assert event.reason_quote in documents[event.document_id].text
        assert event.review_status == "auto"

    yk = [e for e in events if e.broker_id == brokers["yapi-kredi-yatirim"].id]
    assert [(e.ticker, e.action, e.recommendation, e.target_price) for e in yk] == [
        ("VAKBN", "removed", "AL", 41.0)
    ]
    isy = sorted(
        (e.event_date, e.ticker, e.action)
        for e in events
        if e.broker_id == brokers["is-yatirim"].id
    )
    assert isy == [
        (date(2026, 9, 1), "AKBNK", "weight_up"),
        (date(2026, 9, 1), "ENJSA", "weight_up"),
        (date(2026, 9, 1), "GARAN", "weight_up"),
        (date(2026, 9, 1), "TUPRS", "weight_down"),
        (date(2026, 10, 20), "AKBNK", "removed"),
        (date(2026, 10, 20), "SAHOL", "added"),
    ]

    snapshots = session.exec(select(ModelPortfolioSnapshot)).all()
    by_broker = {s.broker_id: json.loads(s.items_json) for s in snapshots}
    gedik_items = by_broker[brokers["gedik-yatirim"].id]
    assert [i["ticker"] for i in gedik_items] == [t for _, t in fx.GEDIK_COMPOSITION_ROWS]
    yk_items = {i["ticker"]: i["entry_date"] for i in by_broker[brokers["yapi-kredi-yatirim"].id]}
    assert yk_items["BIMAS"] == "2023-01-16"
    assert all(d.parse_status == "parsed" for d in documents.values())
    # Warnings are persisted even for parsed documents (TCMB skipped in the 1 Sep note).
    weight_note = next(d for d in documents.values() if d.file_url == ISY_1_SEP.file_url)
    assert "TCMB" in weight_note.parse_error


def test_weight_clause_without_ticker_marks_document_needs_review(session: Session):
    text = (
        "Model Portföy Değişiklikler Örnek Şirket’in ağırlığını %5’e indiriyoruz. "
        "Eklenen Şirket SAHOL.IS 05.10.2026"
    )
    note = _doc(CHANGE_NOTE, f"{ISY_URL}/05_10_2026.pdf", date(2026, 10, 5))
    fetcher = FakeFetcher({"is-yatirim": [note]}, texts={**TEXTS, note: text})

    summary = service.run_once(session.get_bind(), load_broker_config(), fetcher=fetcher)

    assert summary.status == "success" and summary.events_new == 1
    document = session.exec(select(BrokerDocument)).one()
    assert document.parse_status == "needs_review"
    assert "weight clause without a known ticker" in document.parse_error
    event = session.exec(select(ModelPortfolioEvent)).one()
    assert (event.ticker, event.action, event.review_status) == ("SAHOL", "added", "auto")


def test_composition_without_table_is_failed_and_counted(session: Session):
    texts = {**TEXTS, GEDIK_PAGE: "<html><body><p>Model Portföy</p></body></html>"}
    fetcher = FakeFetcher({"gedik-yatirim": [GEDIK_PAGE, GEDIK_22_SEP]}, texts=texts)

    summary = service.run_once(session.get_bind(), load_broker_config(), fetcher=fetcher)

    assert summary.status == "partial_fail" and summary.errors_count == 1
    assert (summary.documents_new, summary.events_new, summary.snapshots_new) == (2, 2, 0)
    page = session.exec(select(BrokerDocument).where(BrokerDocument.kind == COMPOSITION)).one()
    assert page.parse_status == "failed"
    assert page.parse_error == "composition table not found"
    results = {r.broker_slug: r for r in session.exec(select(BrokerWatchSourceResult)).all()}
    result = results["gedik-yatirim"]
    assert result.status == "partial" and result.errors_count == 1
    assert "composition table not found" in result.error_detail


def test_unknown_ticker_event_is_stored_as_needs_review(session: Session):
    unknown = _doc(CHANGE_NOTE, "https://cdn.gedik.com/x/modelportfoyguncellemesi_01102026.pdf")
    texts = {**TEXTS, unknown: fx.GEDIK_UNKNOWN_TICKER_TEXT}
    fetcher = FakeFetcher({"gedik-yatirim": [unknown]}, texts=texts)

    summary = service.run_once(session.get_bind(), load_broker_config(), fetcher=fetcher)

    assert summary.status == "success" and summary.events_new == 1
    event = session.exec(select(ModelPortfolioEvent)).one()
    assert (event.ticker, event.action, event.review_status) == ("ORNEK", "added", "needs_review")
    document = session.get(BrokerDocument, event.document_id)
    assert document.parse_status == "needs_review"
    assert "ORNEK" in (document.parse_error or "")


def test_source_failure_is_recorded_and_does_not_stop_the_run(session: Session):
    class FailingFetcher(FakeFetcher):
        def discover(self, broker: WatchedBroker) -> list[DiscoveredDocument]:
            if broker.slug == "yapi-kredi-yatirim":
                raise RuntimeError("boom")
            return super().discover(broker)

    docs = _default_docs()
    fetcher = FailingFetcher(docs)
    summary = service.run_once(session.get_bind(), load_broker_config(), fetcher=fetcher)

    assert summary.status == "partial_fail"
    assert summary.sources_failed == 1 and summary.errors_count == 1
    assert summary.documents_new == 4
    run = session.get(BrokerWatchRun, summary.run_id)
    assert run.status == "partial_fail"
    assert json.loads(run.error_details) == ["Yapı Kredi Yatırım: discover: RuntimeError: boom"]
    results = {r.broker_slug: r for r in session.exec(select(BrokerWatchSourceResult)).all()}
    assert results["yapi-kredi-yatirim"].status == "failed"
    assert results["is-yatirim"].status == "success"
