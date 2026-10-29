import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session

from src.jobs.broker_watch import service
from src.jobs.broker_watch.config import load_broker_config
from src.main import app
from tests.broker_watch import fixtures as fx
from tests.broker_watch.test_service import GEDIK_PAGE, TEXTS, FakeFetcher, _default_docs

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


async def test_health_verdict_is_partial_after_a_partial_run(client: AsyncClient, session: Session):
    engine = session.get_bind()
    config = load_broker_config()
    service.run_once(engine, config, fetcher=FakeFetcher(_default_docs()))  # all sources succeed
    no_table = {**TEXTS, GEDIK_PAGE: "<html><body><p>Model Portföy</p></body></html>"}
    service.run_once(engine, config, fetcher=FakeFetcher(_default_docs(), texts=no_table))

    body = (await client.get("/jobs/broker-watch/health")).json()

    assert body["verdict"] == "partial"
    assert body["stale_sources"] == []
    assert body["last_24h"]["runs_partial"] == 1
    assert body["latest_run"]["status"] == "partial_fail"


async def test_health_reports_per_source_counters_and_skipped_runs(
    client: AsyncClient, session: Session
):
    engine = session.get_bind()
    config = load_broker_config()
    row = "<tr><td>Astor Enerji</td><td>ASTOR</td><td>1₺</td><td>%1</td><td>%1</td></tr>"
    no_astor = {**TEXTS, GEDIK_PAGE: fx.GEDIK_COMPOSITION_HTML.replace(row, "")}
    first = service.run_once(engine, config, fetcher=FakeFetcher(_default_docs(), texts=no_astor))

    body = (await client.get("/jobs/broker-watch/health")).json()
    sources = {s["slug"]: s for s in body["sources"]}
    assert body["verdict"] == "healthy"  # a flagged event is a review item, not an error
    assert sources["gedik-yatirim"]["events_flagged"] == 1
    assert all(s["documents_skipped_robots"] == 0 for s in body["sources"])
    detail = (await client.get(f"/jobs/broker-watch/runs/{first.run_id}")).json()
    assert [s["events_flagged"] for s in detail["sources"]] == [0, 0, 1]

    service.run_once(engine, config, fetcher=FakeFetcher(_default_docs()))
    skipped = service.run_once(engine, config, fetcher=FakeFetcher(_default_docs()))  # 3rd of 2
    assert skipped.status == "skipped_rate_limit"
    body = (await client.get("/jobs/broker-watch/health")).json()
    assert body["latest_run"]["status"] == "skipped_rate_limit"
    assert (body["last_24h"]["runs_total"], body["last_24h"]["runs_skipped"]) == (3, 1)
    assert body["last_24h"]["runs_failed"] == 0 and body["verdict"] == "healthy"
    sources = {s["slug"]: s for s in body["sources"]}
    assert sources["gedik-yatirim"]["events_flagged"] == 0  # counters of the last real scan


@pytest.fixture
def _isolated_backfill_endpoint(monkeypatch):
    """The backfill endpoint must never reach the real service (live HTTP) in tests;
    a test that wants a run installs its own fake. Module state is reset around each test."""
    from src.jobs.broker_watch import router as broker_router

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the real broker-watch run must not start in tests")

    monkeypatch.setattr(broker_router.service, "run_once", must_not_run)
    broker_router._backfill_state.clear()
    broker_router._backfill_state["running"] = False
    yield
    if broker_router._backfill_lock.locked():
        broker_router._backfill_lock.release()
    broker_router._backfill_state.clear()
    broker_router._backfill_state["running"] = False


@pytest.mark.usefixtures("_isolated_backfill_endpoint")
async def test_backfill_endpoint_runs_one_backfill_in_the_background(
    client: AsyncClient, session: Session, monkeypatch
):
    from datetime import date

    from src.jobs.broker_watch import router as broker_router

    calls = []

    def fake_run_once(engine, config, *, since, max_documents):
        calls.append((since, max_documents))
        # the fixture blocks service.run_once; call the run itself with an offline fetcher
        return service._run_once(
            session.get_bind(), config, FakeFetcher(_default_docs()), since, max_documents
        )

    monkeypatch.setattr(broker_router.service, "run_once", fake_run_once)

    response = await client.post("/jobs/broker-watch/backfill", params={"since": "2026-01-01"})

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "since": "2026-01-01", "max_documents": 40}
    assert calls == [(date(2026, 1, 1), 40)]  # background task ran after the response
    state = (await client.get("/jobs/broker-watch/backfill")).json()
    assert state["running"] is False and state["status"] == "success" and state["error"] is None
    runs = (await client.get("/jobs/broker-watch/runs")).json()
    assert runs["count"] == 1
    assert runs["runs"][0]["error_details"] == ["backfill since 2026-01-01"]


@pytest.mark.usefixtures("_isolated_backfill_endpoint")
async def test_backfill_endpoint_reports_a_failed_run(client: AsyncClient, monkeypatch):
    from src.jobs.broker_watch import router as broker_router

    def boom(*_args, **_kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(broker_router.service, "run_once", boom)

    response = await client.post("/jobs/broker-watch/backfill", params={"since": "2026-01-01"})
    assert response.status_code == 202
    state = (await client.get("/jobs/broker-watch/backfill")).json()
    assert state["running"] is False
    assert state["status"] == "error" and "database unreachable" in state["error"]
    # the lock was released: a second request is accepted again
    again = await client.post("/jobs/broker-watch/backfill", params={"since": "2026-01-01"})
    assert again.json()["status"] == "accepted"


@pytest.mark.usefixtures("_isolated_backfill_endpoint")
async def test_backfill_endpoint_refuses_a_second_run_while_one_is_running(
    client: AsyncClient,
):
    from src.jobs.broker_watch import router as broker_router

    assert broker_router._backfill_lock.acquire(blocking=False)
    broker_router._backfill_state.update(running=True, since="2026-01-01")
    try:
        response = await client.post("/jobs/broker-watch/backfill", params={"since": "2026-01-01"})
        assert response.status_code == 202
        assert response.json()["status"] == "already_running"
    finally:
        broker_router._backfill_state["running"] = False
        broker_router._backfill_lock.release()


@pytest.mark.usefixtures("_isolated_backfill_endpoint")
async def test_backfill_endpoint_validates_input_and_requires_the_key(client: AsyncClient):
    assert (await client.post("/jobs/broker-watch/backfill")).status_code == 422  # since required
    future = await client.post("/jobs/broker-watch/backfill", params={"since": "2999-01-01"})
    assert future.status_code == 422
    too_many = await client.post(
        "/jobs/broker-watch/backfill", params={"since": "2026-01-01", "max_documents": 500}
    )
    assert too_many.status_code == 422
    anonymous = await client.post(
        "/jobs/broker-watch/backfill",
        params={"since": "2026-01-01"},
        headers={"X-API-Key": ""},
    )
    assert anonymous.status_code == 401
