from httpx import ASGITransport, AsyncClient
from sqlmodel import Session

from src.jobs.broker_watch import service
from src.jobs.broker_watch.config import load_broker_config
from src.main import app
from tests.broker_watch.test_service import FakeFetcher, _default_docs

ALL_SLUGS = ["is-yatirim", "yapi-kredi-yatirim", "gedik-yatirim"]


async def test_health_without_runs_flags_every_source_stale(client: AsyncClient):
    response = await client.get("/jobs/broker-watch/health")

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "no_runs"
    assert body["latest_run"] is None
    assert body["last_24h"]["runs_total"] == 0
    assert body["stale_sources"] == ALL_SLUGS
    assert [s["slug"] for s in body["sources"]] == ALL_SLUGS
    assert all(s["last_success_at"] is None for s in body["sources"])
    assert body["config"]["brokers_enabled"] == ALL_SLUGS
    assert body["config"]["max_runs_per_day"] == 2


async def test_health_after_successful_run(client: AsyncClient, session: Session):
    fetcher = FakeFetcher(_default_docs())
    summary = service.run_once(session.get_bind(), load_broker_config(), fetcher=fetcher)

    response = await client.get("/jobs/broker-watch/health")

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "healthy"
    assert body["stale_sources"] == []
    assert body["latest_run"]["id"] == summary.run_id
    assert body["latest_run"]["status"] == "success"
    assert body["last_24h"]["events_new"] == summary.events_new
    assert all(s["last_success_at"] for s in body["sources"])


async def test_list_and_get_runs(client: AsyncClient, session: Session):
    empty = await client.get("/jobs/broker-watch/runs")
    assert empty.status_code == 200 and empty.json() == {"count": 0, "runs": []}

    fetcher = FakeFetcher(_default_docs())
    summary = service.run_once(session.get_bind(), load_broker_config(), fetcher=fetcher)

    listed = await client.get("/jobs/broker-watch/runs")
    assert listed.json()["count"] == 1
    assert listed.json()["runs"][0]["documents_new"] == summary.documents_new

    detail = await client.get(f"/jobs/broker-watch/runs/{summary.run_id}")
    assert detail.status_code == 200
    assert [s["broker_slug"] for s in detail.json()["sources"]] == ALL_SLUGS
    assert all(s["status"] == "success" for s in detail.json()["sources"])

    missing = await client.get("/jobs/broker-watch/runs/9999")
    assert missing.status_code == 404


async def test_routes_require_api_key(client: AsyncClient):
    # The `client` fixture keeps the dependency override; this client sends no key.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        for path in ("health", "runs", "runs/1"):
            response = await anon.get(f"/jobs/broker-watch/{path}")
            assert response.status_code == 401, path
