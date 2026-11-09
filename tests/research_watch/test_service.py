"""run_once with fakes: detection window, idempotency, daily limit, backfill, digest retries."""

import json
from datetime import date

import httpx
import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from src.jobs.research_watch import service
from src.jobs.research_watch.fetcher import HttpFetcher
from src.jobs.research_watch.models import ResearchItem, ResearchWatchRun
from tests.research_watch.helpers import (
    BASE,
    NOW,
    POST_2025_12_23,
    POST_2026_11_04,
    FakeFetcher,
    config,
    good_digest_fn,
    read,
)


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(service, "utc_now", lambda: NOW)
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _items(engine) -> list[ResearchItem]:
    with Session(engine) as session:
        return list(session.exec(select(ResearchItem).order_by(ResearchItem.published_at)))


def test_normal_run_takes_only_recent_posts_and_waits_for_review(engine):
    fetcher = FakeFetcher()
    summary = service.run_once(engine, config(), fetcher, good_digest_fn)
    assert summary.status == "success"
    assert (summary.items_seen, summary.items_new, summary.digests_ok) == (1, 1, 1)
    assert fetcher.urls == [BASE, POST_2026_11_04]
    [item] = _items(engine)
    assert item.published_at == date(2026, 11, 4)
    assert json.loads(item.authors) == ["Ünal Seven", "Kadir Gürci"]
    assert item.checks_passed is True
    assert item.review_status == "needs_review"  # auto_publish is off until the evaluation
    assert json.loads(item.digest_json)["topics"] == ["faiz-para-politikasi"]


def test_second_run_is_idempotent_and_third_is_rate_limited(engine):
    service.run_once(engine, config(), FakeFetcher(), good_digest_fn)
    fetcher = FakeFetcher()
    second = service.run_once(engine, config(), fetcher, good_digest_fn)
    assert (second.status, second.items_new, fetcher.urls) == ("success", 0, [BASE])
    third = service.run_once(engine, config(), FakeFetcher(), good_digest_fn)
    assert third.status == service.SKIPPED_RATE_LIMIT
    assert len(_items(engine)) == 1


def test_auto_publish_serves_only_digests_that_pass(engine):
    service.run_once(engine, config(auto_publish=True), FakeFetcher(), good_digest_fn)
    assert _items(engine)[0].review_status == "auto"


def test_failed_check_is_stored_for_review_even_with_auto_publish(engine):
    def invented(**kw):
        return {**good_digest_fn(**kw), "headline": "Fonlarda 999 milyar TL var"}

    summary = service.run_once(engine, config(auto_publish=True), FakeFetcher(), invented)
    assert (summary.digests_ok, summary.digests_flagged) == (0, 1)
    [item] = _items(engine)
    assert item.review_status == "needs_review" and item.checks_passed is False
    assert item.digest_error == "numbers not in source: 999"


def test_backfill_takes_oldest_first_up_to_the_cap_and_notes_the_rest(engine):
    summary = service.run_once(
        engine, config(), FakeFetcher(), good_digest_fn, since=date(2025, 12, 1), max_items=2
    )
    assert summary.items_seen == 5 and summary.items_new == 2
    assert summary.status == "success"
    assert any("only the oldest 2 taken" in n for n in summary.notes)
    assert [i.url for i in _items(engine)][0] == POST_2025_12_23
    with Session(engine) as session:
        run = session.exec(select(ResearchWatchRun)).one()
    assert json.loads(run.error_details)[0] == "backfill since 2025-12-01"
    assert run.errors_count == 0


def test_digest_errors_are_retried_up_to_the_limit(engine):
    def broken(**_kw):
        raise RuntimeError("MiniMax 402 insufficient balance")

    first = service.run_once(engine, config(max_runs_per_day=10), FakeFetcher(), broken)
    assert first.status == "partial_fail"  # the post is stored, its digest is not
    [item] = _items(engine)
    assert (item.review_status, item.digest_attempts) == ("failed", 1)
    for _ in range(3):
        service.run_once(engine, config(max_runs_per_day=10), FakeFetcher(), broken)
    assert _items(engine)[0].digest_attempts == service.MAX_DIGEST_ATTEMPTS
    service.run_once(engine, config(max_runs_per_day=10), FakeFetcher(), good_digest_fn)
    assert _items(engine)[0].review_status == "failed"  # attempts exhausted: redigest by hand


def test_layout_change_fails_the_run_without_storing(engine):
    class Empty(FakeFetcher):
        def get_text(self, url):
            return "<html><body>bakım çalışması</body></html>"

    summary = service.run_once(engine, config(), Empty(), good_digest_fn)
    assert summary.status == "failed"
    assert "listing parsed to 0 posts" in summary.errors[0]
    assert _items(engine) == []


def _transport(robots: str | None, seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.url.path} {request.headers['user-agent']}")
        if request.url.path == "/robots.txt":
            return httpx.Response(404) if robots is None else httpx.Response(200, text=robots)
        if str(request.url) == BASE:
            return httpx.Response(200, text=read("listing.html"))
        return httpx.Response(200, text=read("article_2026_11_04.html"))

    return httpx.MockTransport(handler)


def test_http_fetcher_uses_our_user_agent_and_no_environment_proxy(engine, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    seen: list[str] = []
    with HttpFetcher("dovizveri-bot/0.1", request_delay=0, transport=_transport(None, seen)) as f:
        summary = service.run_once(engine, config(), f, good_digest_fn)
        assert f._client.trust_env is False  # HTTPS_PROXY above is never used
    assert summary.items_new == 1
    assert all(line.endswith("dovizveri-bot/0.1") for line in seen)
    assert seen[0].startswith("/robots.txt")  # missing robots.txt (404) allows


def test_robots_disallow_stops_the_source(engine):
    seen: list[str] = []
    robots = "User-agent: *\nDisallow: /wps/\n"
    with HttpFetcher("dovizveri-bot/0.1", request_delay=0, transport=_transport(robots, seen)) as f:
        summary = service.run_once(engine, config(), f, good_digest_fn)
    assert summary.status == "failed" and "RobotsDisallowed" in summary.errors[0]
    assert len(seen) == 1  # only robots.txt was requested
