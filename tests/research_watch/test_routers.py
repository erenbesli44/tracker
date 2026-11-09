"""Operator and public research APIs: only auto/reviewed digests are served, never body text."""

import json
from datetime import date

import pytest
from sqlmodel import Session

from src.jobs.research_watch import public_router, router
from src.jobs.research_watch.models import ResearchItem
from tests.research_watch.helpers import GOOD_DIGEST, NOW, POST_2026_11_04, config


@pytest.fixture(autouse=True)
def fixed_clock_and_config(monkeypatch):
    monkeypatch.setattr(public_router, "utc_now", lambda: NOW)
    monkeypatch.setattr(public_router, "load_research_config", lambda: config())
    monkeypatch.setattr(router, "load_research_config", lambda: config())


def _add(session: Session, url: str, status: str, digest: dict | None = GOOD_DIGEST) -> int:
    item = ResearchItem(
        source_slug="tcmb-blog",
        url=url,
        title="Fon Tasfiyeleri ve Reel Sektör",
        authors=json.dumps(["Ünal Seven", "Kadir Gürci"], ensure_ascii=False),
        category="Finansal Piyasalar",
        published_at=date(2026, 11, 4),
        body_text="GİZLİ GÖVDE METNİ",
        sha256="x" * 64,
        digest_json=json.dumps(digest, ensure_ascii=False) if digest else None,
        checks_passed=True,
        review_status=status,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item.id


async def test_public_list_serves_only_auto_and_reviewed(client, session):
    _add(session, POST_2026_11_04, "reviewed")
    _add(session, POST_2026_11_04 + "-2", "auto")
    hidden = _add(session, POST_2026_11_04 + "-3", "needs_review")
    _add(session, POST_2026_11_04 + "-4", "rejected")
    response = await client.get("/research/digests")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    item = body["items"][0]
    assert item["source"]["disclaimer"].startswith("Yazarların görüşüdür")
    assert item["authors"] == ["Ünal Seven", "Kadir Gürci"]
    assert item["digest"]["headline"] == GOOD_DIGEST["headline"]
    assert "GİZLİ" not in response.text  # body text is never served
    assert (await client.get(f"/research/digests/{hidden}")).status_code == 404


async def test_public_topic_filter_and_unknown_topic(client, session):
    _add(session, POST_2026_11_04, "reviewed", {**GOOD_DIGEST, "topics": ["enflasyon"]})
    assert (await client.get("/research/digests?topic=enflasyon")).json()["count"] == 1
    assert (await client.get("/research/digests?topic=altin")).json()["count"] == 0
    assert (await client.get("/research/digests?topic=xyz")).status_code == 422


async def test_review_queue_and_decision(client, session):
    item_id = _add(session, POST_2026_11_04, "needs_review")
    queue = (await client.get("/jobs/research-watch/items?status=needs_review")).json()
    assert queue["count"] == 1 and queue["items"][0]["checks_passed"] is True
    response = await client.patch(
        f"/jobs/research-watch/items/{item_id}", json={"review_status": "reviewed"}
    )
    assert response.json() == {"id": item_id, "review_status": "reviewed"}
    assert (await client.get(f"/research/digests/{item_id}")).status_code == 200
    bad = await client.patch(f"/jobs/research-watch/items/{item_id}", json={"review_status": "x"})
    assert bad.status_code == 422


async def test_cannot_approve_a_post_without_digest(client, session):
    item_id = _add(session, POST_2026_11_04, "failed", digest=None)
    response = await client.patch(
        f"/jobs/research-watch/items/{item_id}", json={"review_status": "reviewed"}
    )
    assert response.status_code == 409


async def test_run_trigger_validates_input(client):
    assert (await client.post("/jobs/research-watch/run?max_items=3")).status_code == 422
    assert (await client.post("/jobs/research-watch/run?since=2999-01-01")).status_code == 422


async def test_api_key_is_required(client):
    response = await client.get("/research/digests", headers={"X-API-Key": "wrong"})
    assert response.status_code in (401, 403)
