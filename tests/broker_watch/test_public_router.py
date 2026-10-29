"""Read-only broker API (OI-027 step 4): /brokers/, /brokers/model-portfolio/events,
/brokers/{slug}/model-portfolio."""

import json
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session

from src.jobs.broker_watch import service
from src.jobs.broker_watch.adapters import YK_RESEARCH_PAGE
from src.jobs.broker_watch.config import load_broker_config
from src.jobs.broker_watch.models import (
    Broker,
    BrokerDocument,
    ModelPortfolioEvent,
    ModelPortfolioSnapshot,
)
from src.main import app
from src.models import utc_now
from tests.broker_watch.test_service import FakeFetcher, _default_docs

TODAY = utc_now().date()
ISY_SITE = "https://arastirma.isyatirim.com.tr"
EVENT_KEYS = {
    "id",
    "broker",
    "ticker",
    "action",
    "event_date",
    "reason_quote",
    "quote_page",
    "recommendation",
    "target_price",
    "document",
    "previous",
}


def _day(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


@pytest.fixture
def data(session: Session) -> dict:
    """Two configured brokers plus one absent from the config, with events of every
    review status. Returns ids by label."""
    isy = Broker(slug="is-yatirim", name="İş Yatırım", site_url=ISY_SITE, adapter="isy_rss")
    gedik = Broker(
        slug="gedik-yatirim", name="Gedik Yatırım", site_url="https://gedik.com", adapter="x"
    )
    old = Broker(slug="old-broker", name="Old", site_url="https://old.example", adapter="x")
    session.add_all([isy, gedik, old])
    session.commit()

    def doc(broker: Broker, n: int, file_url: str | None, days_ago: int | None) -> BrokerDocument:
        d = BrokerDocument(
            broker_id=broker.id,
            kind="change_note",
            source_url=f"{broker.site_url}/post-{n}/",
            file_url=file_url,
            sha256=f"{n:064d}",
            published_at=TODAY - timedelta(days=days_ago) if days_ago is not None else None,
            text="never served",
        )
        session.add(d)
        session.commit()
        return d

    isy_old = doc(isy, 1, f"{ISY_SITE}/wp-content/uploads/a.pdf", 200)
    isy_new = doc(isy, 2, f"{ISY_SITE}/wp-content/uploads/b.pdf", 5)
    gedik_doc = doc(gedik, 3, None, None)

    def event(broker, document, ticker, action, days_ago, status="auto", **kw):
        e = ModelPortfolioEvent(
            broker_id=broker.id,
            document_id=document.id,
            ticker=ticker,
            action=action,
            event_date=TODAY - timedelta(days=days_ago),
            reason_quote=f"{ticker} {action} quote",
            quote_page=1,
            review_status=status,
            **kw,
        )
        session.add(e)
        session.commit()
        return e

    ids = {
        "sahol_added": event(isy, isy_old, "SAHOL", "added", 200).id,
        "sahol_removed": event(isy, isy_new, "SAHOL", "removed", 5, "reviewed").id,
        "akbnk_up": event(isy, isy_new, "AKBNK", "weight_up", 5).id,
        "akbnk_flagged": event(isy, isy_new, "AKBNK", "removed", 3, "needs_review").id,
        "garan_rejected": event(isy, isy_new, "GARAN", "added", 2, "rejected").id,
        "astor_added": event(
            gedik, gedik_doc, "ASTOR", "added", 10, recommendation="AL", target_price=41.0
        ).id,
    }
    session.add_all(
        [
            ModelPortfolioSnapshot(
                broker_id=gedik.id,
                document_id=gedik_doc.id,
                as_of=TODAY - timedelta(days=30),
                items_json=json.dumps([{"ticker": "OLD", "entry_date": None}]),
            ),
            ModelPortfolioSnapshot(
                broker_id=gedik.id,
                document_id=gedik_doc.id,
                as_of=TODAY - timedelta(days=1),
                basis="current",
                items_json=json.dumps(
                    [
                        {
                            "ticker": "ASTOR",
                            "entry_date": _day(10),
                            "target_price": 120.5,
                            "weight": 10,
                        },
                        {"ticker": "THYAO", "entry_date": None, "potential": "%30"},
                    ]
                ),
            ),
        ]
    )
    session.commit()
    return ids


async def test_list_brokers_counts_served_events_only(client: AsyncClient, data: dict):
    response = await client.get("/brokers/")

    assert response.status_code == 200
    assert response.json() == {
        "brokers": [
            {
                "slug": "is-yatirim",
                "name": "İş Yatırım",
                "site_url": ISY_SITE,
                "enabled": True,
                "events_count": 3,  # needs_review and rejected not counted
                "last_event_date": _day(5),
            },
            {
                "slug": "gedik-yatirim",
                "name": "Gedik Yatırım",
                "site_url": "https://gedik.com",
                "enabled": True,
                "events_count": 1,
                "last_event_date": _day(10),
            },
            {
                "slug": "old-broker",
                "name": "Old",
                "site_url": "https://old.example",
                "enabled": False,  # not in watched_brokers.yaml
                "events_count": 0,
                "last_event_date": None,
            },
        ]
    }


async def test_events_newest_first_with_exact_shape(client: AsyncClient, data: dict):
    response = await client.get("/brokers/model-portfolio/events")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"count", "events"}
    # default 90 days: SAHOL added (200 days ago) is outside; flagged/rejected never served
    ids = [e["id"] for e in body["events"]]
    assert ids == [data["akbnk_up"], data["sahol_removed"], data["astor_added"]]
    assert body["count"] == 3
    assert all(set(e) == EVENT_KEYS for e in body["events"])

    akbnk, sahol, astor = body["events"]
    assert akbnk["event_date"] == sahol["event_date"]  # same date: id desc
    assert akbnk["id"] > sahol["id"]
    assert sahol == {
        "id": data["sahol_removed"],
        "broker": {"slug": "is-yatirim", "name": "İş Yatırım"},
        "ticker": "SAHOL",
        "action": "removed",
        "event_date": _day(5),
        "reason_quote": "SAHOL removed quote",
        "quote_page": 1,
        "recommendation": None,
        "target_price": None,
        "document": {
            "url": f"{ISY_SITE}/wp-content/uploads/b.pdf",
            "page_url": f"{ISY_SITE}/post-2/",  # the post that links the PDF
            "published_at": _day(5),
        },
        # computed even though the earlier event is outside the days window
        "previous": {"action": "added", "event_date": _day(200)},
    }
    assert akbnk["previous"] is None
    # no file_url: the document URL falls back to source_url, so no separate page_url
    assert astor["document"] == {
        "url": "https://gedik.com/post-3/",
        "page_url": None,
        "published_at": None,
    }
    assert (astor["recommendation"], astor["target_price"]) == ("AL", 41.0)


async def test_previous_ignores_unserved_events(client: AsyncClient, session: Session, data):
    isy = session.get(ModelPortfolioEvent, data["akbnk_up"])
    later = ModelPortfolioEvent(
        broker_id=isy.broker_id,
        document_id=isy.document_id,
        ticker="AKBNK",
        action="weight_down",
        event_date=TODAY - timedelta(days=1),
        reason_quote="q",
    )
    session.add(later)
    session.commit()

    events = (await client.get("/brokers/model-portfolio/events")).json()["events"]

    assert events[0]["id"] == later.id
    # the needs_review AKBNK "removed" 3 days ago is skipped; previous is the weight_up
    assert events[0]["previous"] == {"action": "weight_up", "event_date": _day(5)}


async def test_events_days_broker_and_limit_filters(client: AsyncClient, data: dict):
    wide = (await client.get("/brokers/model-portfolio/events", params={"days": 365})).json()
    assert [e["id"] for e in wide["events"]][-1] == data["sahol_added"]
    assert wide["count"] == 4
    assert wide["events"][-1]["previous"] is None

    narrow = (await client.get("/brokers/model-portfolio/events", params={"days": 7})).json()
    assert [e["id"] for e in narrow["events"]] == [data["akbnk_up"], data["sahol_removed"]]

    gedik = (
        await client.get("/brokers/model-portfolio/events", params={"broker": "gedik-yatirim"})
    ).json()
    assert [e["id"] for e in gedik["events"]] == [data["astor_added"]]

    limited = (
        await client.get("/brokers/model-portfolio/events", params={"days": 365, "limit": 2})
    ).json()
    assert limited["count"] == 2
    assert [e["id"] for e in limited["events"]] == [data["akbnk_up"], data["sahol_removed"]]


async def test_events_unknown_broker_is_404(client: AsyncClient, data: dict):
    response = await client.get("/brokers/model-portfolio/events", params={"broker": "nope"})
    assert response.status_code == 404
    assert response.json() == {"detail": "broker_not_found"}


@pytest.mark.parametrize(
    "params",
    [{"days": 0}, {"days": 3651}, {"limit": 0}, {"limit": 501}, {"days": "x"}],
)
async def test_events_validation(client: AsyncClient, params: dict):
    response = await client.get("/brokers/model-portfolio/events", params=params)
    assert response.status_code == 422


async def test_broker_model_portfolio_strips_composition(client: AsyncClient, data: dict):
    response = await client.get("/brokers/gedik-yatirim/model-portfolio")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"broker", "composition", "events"}
    assert body["broker"] == {
        "slug": "gedik-yatirim",
        "name": "Gedik Yatırım",
        "site_url": "https://gedik.com",
    }
    assert body["composition"] == {
        "as_of": _day(1),
        "basis": "current",
        "items": [
            {"ticker": "ASTOR", "entry_date": _day(10)},
            {"ticker": "THYAO", "entry_date": None},
        ],
    }
    assert [e["id"] for e in body["events"]] == [data["astor_added"]]


async def test_broker_model_portfolio_without_snapshot_and_last_ten(
    client: AsyncClient, session: Session, data: dict
):
    first = session.get(ModelPortfolioEvent, data["sahol_added"])
    for n in range(12):  # old events, outside any days window: no days filter here
        session.add(
            ModelPortfolioEvent(
                broker_id=first.broker_id,
                document_id=first.document_id,
                ticker=f"T{n}",
                action="added",
                event_date=TODAY - timedelta(days=1000 + n),
                reason_quote="q",
            )
        )
    session.commit()

    body = (await client.get("/brokers/is-yatirim/model-portfolio")).json()

    assert body["composition"] is None
    assert len(body["events"]) == 10
    assert [e["id"] for e in body["events"][:3]] == [
        data["akbnk_up"],
        data["sahol_removed"],
        data["sahol_added"],
    ]
    statuses_served = {e["id"] for e in body["events"]}
    assert data["akbnk_flagged"] not in statuses_served
    assert data["garan_rejected"] not in statuses_served


async def test_broker_model_portfolio_unknown_slug_is_404(client: AsyncClient):
    response = await client.get("/brokers/nope/model-portfolio")
    assert response.status_code == 404
    assert response.json() == {"detail": "broker_not_found"}


async def test_served_document_urls_are_on_the_broker_domain(client: AsyncClient, session: Session):
    """Criterion C on data stored by a real (offline) run: every document URL is on the
    broker's own host, or Gedik's CDN."""
    service.run_once(session.get_bind(), load_broker_config(), fetcher=FakeFetcher(_default_docs()))
    sites = {b["slug"]: b["site_url"] for b in (await client.get("/brokers/")).json()["brokers"]}

    events = (
        await client.get("/brokers/model-portfolio/events", params={"days": 3650, "limit": 500})
    ).json()["events"]

    assert events
    for event in events:
        host = urlsplit(event["document"]["url"]).hostname
        site_host = urlsplit(sites[event["broker"]["slug"]]).hostname
        allowed = {site_host, "cdn.gedik.com"} if site_host == "gedik.com" else {site_host}
        assert host in allowed, event["document"]["url"]
        assert event["broker"]["slug"] in sites
        assert len(event["reason_quote"]) <= 300


async def test_broker_routes_require_api_key(client: AsyncClient):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for path in ("/brokers/", "/brokers/model-portfolio/events", "/brokers/x/model-portfolio"):
            response = await anon.get(path)
            assert response.status_code == 401, path


async def test_composition_drops_items_without_ticker(
    client: AsyncClient, session: Session, data: dict
):
    gedik = session.get(ModelPortfolioEvent, data["astor_added"])
    session.add(
        ModelPortfolioSnapshot(
            broker_id=gedik.broker_id,
            document_id=gedik.document_id,
            as_of=TODAY,
            items_json=json.dumps(
                [
                    {"ticker": "ASTOR", "entry_date": None},
                    {"ticker": None, "entry_date": _day(3)},
                    {"ticker": "", "entry_date": None},
                    {"entry_date": _day(4)},
                    "not-an-object",
                ]
            ),
        )
    )
    session.commit()

    body = (await client.get("/brokers/gedik-yatirim/model-portfolio")).json()

    assert body["composition"]["as_of"] == TODAY.isoformat()
    assert body["composition"]["items"] == [{"ticker": "ASTOR", "entry_date": None}]


async def test_yapi_kredi_page_url_is_the_research_page(client: AsyncClient, session: Session):
    """Yapı Kredi's GetFilePath PDF URL returns 400 without the research page's cookies and
    Referer; page_url sends the reader to the research page the adapter stores as source_url."""
    yk = Broker(
        slug="yapi-kredi-yatirim",
        name="Yapı Kredi Yatırım",
        site_url="https://www.ykyatirim.com.tr",
        adapter="yk_json",
    )
    session.add(yk)
    session.commit()
    file_url = "https://www.ykyatirim.com.tr/DetailPage/GetFilePath?url=abc"
    document = BrokerDocument(
        broker_id=yk.id,
        kind="change_note",
        source_url=YK_RESEARCH_PAGE,  # what adapters._discover_yk stores
        file_url=file_url,
        sha256="f" * 64,
        published_at=TODAY,
    )
    session.add(document)
    session.commit()
    session.add(
        ModelPortfolioEvent(
            broker_id=yk.id,
            document_id=document.id,
            ticker="VAKBN",
            action="removed",
            event_date=TODAY,
            reason_quote="q",
        )
    )
    session.commit()

    events = (
        await client.get("/brokers/model-portfolio/events", params={"broker": "yapi-kredi-yatirim"})
    ).json()["events"]

    page_url = events[0]["document"]["page_url"]
    assert events[0]["document"]["url"] == file_url  # contract field unchanged
    assert urlsplit(page_url).hostname == "www.ykyatirim.com.tr"
    assert "GetFilePath" not in page_url
    assert page_url == "https://www.ykyatirim.com.tr/hizmetler/arastirma"
