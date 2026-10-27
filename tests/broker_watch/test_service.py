"""run_once with a fake fetcher: no network, idempotent, review flags preserved."""

import json
from datetime import date

import httpx
import pytest
from sqlalchemy import func
from sqlmodel import Session, select

from src.jobs.broker_watch import adapters, service
from src.jobs.broker_watch.adapters import (
    CHANGE_NOTE,
    COMPOSITION,
    DiscoveredDocument,
    FetchedDocument,
    HttpFetcher,
    sha256_hex,
)
from src.jobs.broker_watch.config import BrokerWatchConfig, WatchedBroker, load_broker_config
from src.jobs.broker_watch.models import (
    Broker,
    BrokerDocument,
    BrokerWatchRun,
    BrokerWatchSourceResult,
    ModelPortfolioEvent,
    ModelPortfolioSnapshot,
)
from tests.broker_watch import fixtures as fx
from tests.broker_watch.test_adapters import UA, FakeSites


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


# ── Step 2: snapshot basis, consistency check, robots, rate limit, client close ──


def _one_broker_config(slug: str) -> BrokerWatchConfig:
    config = load_broker_config()
    return BrokerWatchConfig(
        brokers=[b for b in config.brokers if b.slug == slug], defaults=config.defaults
    )


def _seed(session: Session, slug: str = "gedik-yatirim") -> tuple[Broker, BrokerDocument]:
    broker = Broker(slug=slug, name=slug, site_url="https://example.com", adapter="gedik_page")
    session.add(broker)
    session.commit()
    document = BrokerDocument(
        broker_id=broker.id,
        kind=CHANGE_NOTE,
        source_url="https://example.com/note.pdf",
        sha256=sha256_hex(slug.encode("utf-8")),
        text="metin",
    )
    session.add(document)
    session.commit()
    return broker, document


def _event(session, broker, document, ticker, action, day) -> ModelPortfolioEvent:
    event = ModelPortfolioEvent(
        broker_id=broker.id,
        document_id=document.id,
        ticker=ticker,
        action=action,
        event_date=day,
        reason_quote="metin",
    )
    session.add(event)
    session.commit()
    return event


def _snapshot(session, broker, document, day, tickers, basis="current") -> None:
    session.add(
        ModelPortfolioSnapshot(
            broker_id=broker.id,
            document_id=document.id,
            as_of=day,
            basis=basis,
            items_json=json.dumps([{"ticker": t, "entry_date": None} for t in tickers]),
        )
    )
    session.commit()


def test_snapshot_basis_is_pre_change_for_yk_and_current_for_gedik(session: Session):
    service.run_once(session.get_bind(), load_broker_config(), fetcher=FakeFetcher(_default_docs()))

    brokers = {b.slug: b for b in session.exec(select(Broker)).all()}
    basis = {s.broker_id: s.basis for s in session.exec(select(ModelPortfolioSnapshot)).all()}
    assert basis[brokers["yapi-kredi-yatirim"].id] == "pre_change"
    assert basis[brokers["gedik-yatirim"].id] == "current"


def test_consistency_check_keeps_yk_event_with_same_day_pre_change_snapshot(session: Session):
    broker, document = _seed(session, "yapi-kredi-yatirim")
    event = _event(session, broker, document, "VAKBN", "removed", date(2026, 9, 14))
    _snapshot(session, broker, document, date(2026, 9, 14), ["BIMAS", "VAKBN"], basis="pre_change")

    assert service.check_snapshot_consistency(session, broker.id) == 0
    session.refresh(event)
    assert event.review_status == "auto"


def test_consistency_check_treats_later_pre_change_snapshot_as_current(session: Session):
    broker, document = _seed(session, "yapi-kredi-yatirim")
    event = _event(session, broker, document, "VAKBN", "removed", date(2026, 9, 14))
    _snapshot(session, broker, document, date(2026, 10, 1), ["BIMAS", "VAKBN"], basis="pre_change")

    assert service.check_snapshot_consistency(session, broker.id) == 1
    session.refresh(event)
    assert event.review_status == "needs_review"


def test_consistency_check_flags_events_contradicted_by_next_current_snapshot(session: Session):
    broker, document = _seed(session)
    added = _event(session, broker, document, "ASTOR", "added", date(2026, 9, 22))
    removed = _event(session, broker, document, "TUPRS", "removed", date(2026, 9, 22))
    weight = _event(session, broker, document, "AKSA", "weight_up", date(2026, 9, 22))
    _snapshot(session, broker, document, date(2026, 9, 1), ["ASTOR"])  # earlier: ignored
    _snapshot(session, broker, document, date(2026, 10, 26), ["AKSA", "TUPRS"])  # no ASTOR

    assert service.check_snapshot_consistency(session, broker.id) == 2
    for row in (added, removed, weight, document):
        session.refresh(row)
    assert (added.review_status, removed.review_status) == ("needs_review", "needs_review")
    assert weight.review_status == "auto"  # weight events are never checked
    assert document.parse_status == "needs_review"
    assert document.parse_error == (
        "consistency: ASTOR added 2026-09-22 but the current snapshot of 2026-10-26 "
        "does not list it; "
        "consistency: TUPRS removed 2026-09-22 but the current snapshot of 2026-10-26 lists it"
    )
    assert service.check_snapshot_consistency(session, broker.id) == 0  # already flagged


def test_consistency_check_ignores_same_day_current_snapshot(session: Session):
    broker, document = _seed(session)
    event = _event(session, broker, document, "ASTOR", "added", date(2026, 9, 22))
    _snapshot(session, broker, document, date(2026, 9, 22), ["AKSA"])  # same day: no evidence

    assert service.check_snapshot_consistency(session, broker.id) == 0
    session.refresh(event)
    assert event.review_status == "auto"

    _snapshot(session, broker, document, date(2026, 9, 23), ["AKSA"])  # strictly later: evidence
    assert service.check_snapshot_consistency(session, broker.id) == 1


def test_consistency_check_leaves_events_without_later_snapshot_alone(session: Session):
    broker, document = _seed(session)
    event = _event(session, broker, document, "ASTOR", "added", date(2026, 9, 22))
    _snapshot(session, broker, document, date(2026, 9, 1), ["AKSA"])

    assert service.check_snapshot_consistency(session, broker.id) == 0
    session.refresh(event)
    session.refresh(document)
    assert event.review_status == "auto" and document.parse_error is None


def test_run_once_flags_added_ticker_missing_from_composition(session: Session):
    row = "<tr><td>Astor Enerji</td><td>ASTOR</td><td>1₺</td><td>%1</td><td>%1</td></tr>"
    page = fx.GEDIK_COMPOSITION_HTML.replace(row, "")
    assert "ASTOR" not in page
    docs = {"gedik-yatirim": [GEDIK_PAGE, GEDIK_22_SEP]}
    fetcher = FakeFetcher(docs, texts={**TEXTS, GEDIK_PAGE: page})

    summary = service.run_once(
        session.get_bind(), _one_broker_config("gedik-yatirim"), fetcher=fetcher
    )

    assert summary.status == "success"  # a flagged event is a review item, not an error
    assert summary.sources[0].events_flagged == 1
    events = {e.ticker: e for e in session.exec(select(ModelPortfolioEvent)).all()}
    assert events["ASTOR"].review_status == "needs_review"
    assert events["TUPRS"].review_status == "auto"
    document = session.get(BrokerDocument, events["ASTOR"].document_id)
    assert document.parse_status == "needs_review" and "consistency: ASTOR" in document.parse_error
    stored = session.exec(select(BrokerWatchSourceResult)).one()
    assert (stored.events_flagged, stored.documents_skipped_robots) == (1, 0)


def test_yk_snapshot_as_of_falls_back_to_the_document_date(session: Session):
    undated = _doc(CHANGE_NOTE, YK_14_SEP.file_url)  # no reportPostTime in the listing
    texts = {**TEXTS, undated: fx.YK_14_SEP_TEXT}
    fetcher = FakeFetcher({"yapi-kredi-yatirim": [undated]}, texts=texts)

    service.run_once(session.get_bind(), _one_broker_config("yapi-kredi-yatirim"), fetcher=fetcher)

    snapshot = session.exec(select(ModelPortfolioSnapshot)).one()
    assert (snapshot.as_of, snapshot.basis) == (date(2026, 9, 14), "pre_change")
    event = session.exec(select(ModelPortfolioEvent)).one()
    assert (event.event_date, event.review_status) == (date(2026, 9, 14), "auto")


def test_run_once_counts_robots_skips_in_source_result(session: Session):
    site = FakeSites()
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)
    site.add("https://cdn.gedik.com/robots.txt", "User-agent: *\nDisallow: /media/\n")

    with site.fetcher() as fetcher:
        summary = service.run_once(
            session.get_bind(), _one_broker_config("gedik-yatirim"), fetcher=fetcher
        )

    result = summary.sources[0]
    assert (result.documents_seen, result.documents_new, result.snapshots_new) == (3, 1, 1)
    assert result.documents_skipped_robots == 2 and result.errors_count == 2
    assert result.status == "partial"
    assert result.error_detail == f"robots.txt disallows {fx.GEDIK_PDF_6_AUG}"
    assert summary.status == "partial_fail"
    stored = session.exec(select(BrokerWatchSourceResult)).one()
    assert stored.errors_count == 2 and stored.error_detail.startswith("robots.txt disallows")
    assert (stored.documents_skipped_robots, stored.events_flagged) == (2, 0)
    assert fx.GEDIK_PDF_22_SEP not in site.urls() and fx.GEDIK_PDF_6_AUG not in site.urls()


def test_run_once_counts_redirect_to_disallowed_path_as_robots_skip(session: Session):
    site = FakeSites()
    site.add(fx.GEDIK_PAGE_URL, fx.GEDIK_PAGE_HTML)
    site.add("https://cdn.gedik.com/robots.txt", "User-agent: *\nDisallow: /private/\n")
    moved = "https://cdn.gedik.com/private/ModelPortfoyGuncellemesi_22_09_2026.pdf"
    site.add(fx.GEDIK_PDF_22_SEP, "", status=302, headers={"Location": moved})
    site.add(fx.GEDIK_PDF_6_AUG, fx.minimal_pdf(["Anadolu Hayat (ANHYT) cikariyoruz: Sebep."]))

    with site.fetcher() as fetcher:
        summary = service.run_once(
            session.get_bind(), _one_broker_config("gedik-yatirim"), fetcher=fetcher
        )

    result = summary.sources[0]
    assert (result.documents_seen, result.documents_new, result.documents_skipped_robots) == (
        3,
        2,
        1,
    )
    assert result.status == "partial" and result.error_detail == f"robots.txt disallows {moved}"
    assert moved not in site.urls()
    stored = session.exec(select(BrokerWatchSourceResult)).one()
    assert stored.documents_skipped_robots == 1


def test_run_once_closes_the_http_fetcher_it_creates(session: Session, monkeypatch):
    site = FakeSites()  # every URL answers 404: each source fails, the run still finishes
    created: list[HttpFetcher] = []
    created_kwargs: list[dict] = []

    def make_fetcher(user_agent: str, **kwargs) -> HttpFetcher:
        created_kwargs.append(kwargs)
        created.append(HttpFetcher(user_agent, transport=httpx.MockTransport(site), **kwargs))
        return created[-1]

    monkeypatch.setattr(service, "HttpFetcher", make_fetcher)
    summary = service.run_once(session.get_bind(), load_broker_config())

    assert summary.status == "failed" and summary.sources_failed == 3
    assert len(created) == 1
    assert created_kwargs == [{"since": None, "request_delay": 0.0}]  # a normal run: no delay
    assert all(r.headers["User-Agent"] == UA for r in site.requests)
    with pytest.raises(RuntimeError, match="closed"):
        created[0].discover(load_broker_config().brokers[2])


def test_run_once_skips_when_max_runs_per_day_is_reached(session: Session):
    engine = session.get_bind()
    config = load_broker_config()
    assert config.defaults.max_runs_per_day == 2
    fetcher = FakeFetcher(_default_docs())

    service.run_once(engine, config, fetcher=fetcher)
    service.run_once(engine, config, fetcher=fetcher)
    third = service.run_once(engine, config, fetcher=fetcher)

    assert third.status == "skipped_rate_limit" and third.sources_scanned == 0
    run = session.get(BrokerWatchRun, third.run_id)
    assert run.status == "skipped_rate_limit" and run.finished_at is not None
    assert _count(session, BrokerWatchRun) == 3
    assert _count(session, BrokerWatchSourceResult) == 6  # nothing scanned by the third
    assert fetcher.fetched.count(GEDIK_PAGE.source_url) == 2

    # Skipped runs do not count towards the limit.
    config.defaults.max_runs_per_day = 3
    assert service.run_once(engine, config, fetcher=fetcher).status == "success"


# ── Step 3: backfill mode ─────────────────────────────────────────────────────


def test_backfill_bypasses_max_runs_per_day_and_marks_the_run(session: Session):
    engine = session.get_bind()
    config = load_broker_config()
    fetcher = FakeFetcher(_default_docs())
    service.run_once(engine, config, fetcher=fetcher)
    service.run_once(engine, config, fetcher=fetcher)
    assert service.run_once(engine, config, fetcher=fetcher).status == "skipped_rate_limit"

    backfill = service.run_once(engine, config, fetcher=fetcher, since=date(2026, 1, 1))

    assert backfill.status == "success" and backfill.sources_scanned == 3
    run = session.get(BrokerWatchRun, backfill.run_id)
    assert run.status == "success" and run.errors_count == 0
    assert json.loads(run.error_details) == ["backfill since 2026-01-01"]
    assert _count(session, BrokerDocument) == 5  # idempotent: nothing stored twice

    # The marker goes first; source errors follow it.
    failing = FakeFetcher(_default_docs(), texts={**TEXTS, GEDIK_PAGE: "<p>no table</p>"})
    partial = service.run_once(engine, config, fetcher=failing, since=date(2026, 1, 1))
    assert partial.status == "partial_fail"
    details = json.loads(session.get(BrokerWatchRun, partial.run_id).error_details)
    assert details[0] == "backfill since 2026-01-01" and len(details) == 2


def test_backfill_document_cap_fails_the_source_without_fetching(session: Session):
    engine = session.get_bind()
    config = load_broker_config()
    fetcher = FakeFetcher(_default_docs())  # İş 2, Yapı Kredi 1, Gedik 2 documents

    summary = service.run_once(
        engine, config, fetcher=fetcher, since=date(2026, 1, 1), max_documents=1
    )

    results = {r.broker_slug: r for r in summary.sources}
    isy = results["is-yatirim"]  # 2 documents against a cap of 1: nothing fetched, nothing charged
    assert isy.status == "failed" and (isy.documents_seen, isy.documents_new) == (0, 0)
    assert (
        isy.error_detail == "discover: 2 documents exceed the remaining cap of 1; nothing fetched"
    )
    yk = results["yapi-kredi-yatirim"]  # the budget is untouched: the later source still runs
    assert yk.status == "success" and (yk.documents_seen, yk.documents_new) == (1, 1)
    gedik = results["gedik-yatirim"]  # Yapı Kredi used the last document of the budget
    assert gedik.status == "failed"
    assert (
        gedik.error_detail == "discover: 2 documents exceed the remaining cap of 0; nothing fetched"
    )
    assert fetcher.fetched == [YK_14_SEP.file_url]
    assert summary.status == "partial_fail" and summary.sources_failed == 2
    stored = {r.broker_slug: r for r in session.exec(select(BrokerWatchSourceResult)).all()}
    assert (stored["is-yatirim"].status, stored["is-yatirim"].documents_seen) == ("failed", 0)
    assert _count(session, BrokerDocument) == 1

    # Without a cap nothing is held back.
    summary = service.run_once(engine, config, fetcher=fetcher, since=date(2026, 1, 1))
    assert summary.status == "success" and _count(session, BrokerDocument) == 5

    # Already-stored documents are iterated (not fetched) and still count: İş 2 + Yapı
    # Kredi 1 use up a cap of 3, so Gedik's 2 fail again although nothing was downloaded.
    fetched_before = list(fetcher.fetched)
    summary = service.run_once(
        engine, config, fetcher=fetcher, since=date(2026, 1, 1), max_documents=3
    )
    results = {r.broker_slug: r for r in summary.sources}
    assert (results["is-yatirim"].status, results["is-yatirim"].documents_seen) == ("success", 2)
    assert results["gedik-yatirim"].status == "failed"
    assert fetcher.fetched == fetched_before


def test_backfill_builds_the_http_fetcher_with_since_and_delay(session: Session, monkeypatch):
    site = FakeSites()
    created_kwargs: list[dict] = []

    def make_fetcher(user_agent: str, **kwargs) -> HttpFetcher:
        created_kwargs.append(kwargs)
        return HttpFetcher(user_agent, transport=httpx.MockTransport(site), **kwargs)

    monkeypatch.setattr(service, "HttpFetcher", make_fetcher)
    monkeypatch.setattr(service, "BACKFILL_REQUEST_DELAY", 0.25)
    # The hook sleeps before every request after the first whatever the response (the
    # 404s here included), so the delay is stubbed out to keep the test instant.
    monkeypatch.setattr(adapters.time, "sleep", lambda _seconds: None)
    config = load_broker_config()

    service.run_once(session.get_bind(), config, since=date(2026, 1, 1))
    service.run_once(session.get_bind(), config, since=date(2026, 1, 1), request_delay=0.0)

    assert created_kwargs == [
        {"since": date(2026, 1, 1), "request_delay": 0.25},
        {"since": date(2026, 1, 1), "request_delay": 0.0},
    ]
    assert all(r.headers["User-Agent"] == UA for r in site.requests)
