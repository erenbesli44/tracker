from httpx import AsyncClient


async def _build_timeline_fixture(client: AsyncClient) -> tuple[int, int, int, str]:
    """Returns (person_id, channel_id, video_id, topic_slug) after full classify cycle."""
    person_resp = await client.post("/persons/", json={"name": "Taner Genek"})
    person_id = person_resp.json()["id"]

    video_resp = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
            "published_at": "2025-01-15T10:00:00",
        },
    )
    video_id = video_resp.json()["id"]
    channel_id = video_resp.json()["channel_id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript."})

    topic_slug = "bist-turk-piyasalari"
    topics_resp = await client.get("/topics/")
    topic_id = next(t["id"] for t in topics_resp.json() if t["slug"] == topic_slug)
    await client.post(
        f"/videos/{video_id}/classify",
        json={
            "topic_mentions": [
                {
                    "topic_id": topic_id,
                    "summary": "BIST100 düşüş bekleniyor.",
                    "sentiment": "bearish",
                    "key_levels": ["13100", "12900"],
                    "start_time": "00:01:00",
                    "end_time": "00:05:00",
                    "confidence": 0.9,
                }
            ],
        },
    )
    return person_id, channel_id, video_id, topic_slug


async def test_timeline_returns_entries(client: AsyncClient):
    person_id, channel_id, video_id, topic_slug = await _build_timeline_fixture(client)

    topics_resp = await client.get("/topics/")
    topic = next(t for t in topics_resp.json() if t["slug"] == topic_slug)
    topic_id = topic["id"]

    response = await client.get(f"/persons/{person_id}/topics/{topic_id}/timeline")
    assert response.status_code == 200
    data = response.json()
    assert data["person_id"] == person_id
    assert data["channel_id"] == channel_id
    assert data["topic"]["id"] == topic_id
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["sentiment"] == "bearish"
    assert "13100" in entry["key_levels"]
    assert entry["video_id"] == video_id


async def test_timeline_unknown_topic_returns_404(client: AsyncClient):
    person_resp = await client.post("/persons/", json={"name": "Test Person"})
    person_id = person_resp.json()["id"]
    response = await client.get(f"/persons/{person_id}/topics/99999/timeline")
    assert response.status_code == 404


async def test_person_overview(client: AsyncClient):
    person_id, channel_id, _, _ = await _build_timeline_fixture(client)
    response = await client.get(f"/persons/{person_id}/topics/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["person_id"] == person_id
    assert data["channel_id"] == channel_id
    assert len(data["topics"]) >= 1
    topic_summary = data["topics"][0]
    assert "topic" in topic_summary
    assert "mention_count" in topic_summary
    assert "latest_sentiment" in topic_summary


async def test_person_overview_unknown_person_returns_404(client: AsyncClient):
    response = await client.get("/persons/99999/topics/overview")
    assert response.status_code == 404


async def test_channel_timeline_returns_entries(client: AsyncClient):
    _person_id, channel_id, video_id, topic_slug = await _build_timeline_fixture(client)

    topics_resp = await client.get("/topics/")
    topic = next(t for t in topics_resp.json() if t["slug"] == topic_slug)
    topic_id = topic["id"]

    response = await client.get(f"/channels/{channel_id}/topics/{topic_id}/timeline")
    assert response.status_code == 200
    data = response.json()
    assert data["channel_id"] == channel_id
    assert len(data["entries"]) == 1
    assert data["entries"][0]["video_id"] == video_id


async def test_channel_overview(client: AsyncClient):
    _person_id, channel_id, _video_id, _topic_slug = await _build_timeline_fixture(client)
    response = await client.get(f"/channels/{channel_id}/topics/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["channel_id"] == channel_id
    assert len(payload["topics"]) >= 1
