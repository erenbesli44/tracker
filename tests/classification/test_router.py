from httpx import AsyncClient


async def _setup(client: AsyncClient) -> tuple[int, int, int]:
    """Returns (person_id, video_id, borsa_istanbul_topic_id) with transcript uploaded."""
    person_resp = await client.post("/persons/", json={"name": "Taner Genek"})
    person_id = person_resp.json()["id"]
    video_resp = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
        },
    )
    video_id = video_resp.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript içeriği."})
    topics_resp = await client.get("/topics/")
    topic_id = next(t["id"] for t in topics_resp.json() if t["slug"] == "borsa-istanbul")
    return person_id, video_id, topic_id


async def _mention(topic_id: int) -> dict:
    return {
        "topic_id": topic_id,
        "summary": "BIST100 düşüş bekleniyor.",
        "sentiment": "bearish",
        "key_levels": ["13100", "12900"],
        "start_time": "00:01:00",
        "end_time": "00:05:00",
        "confidence": 0.9,
    }


async def test_classify_video(client: AsyncClient):
    person_id, video_id, topic_id = await _setup(client)
    response = await client.post(
        f"/videos/{video_id}/classify",
        json={"topic_mentions": [await _mention(topic_id)]},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["video_id"] == video_id
    assert len(data["mentions"]) == 1
    assert data["mentions"][0]["sentiment"] == "bearish"


async def test_classify_video_replaces_existing(client: AsyncClient):
    person_id, video_id, topic_id = await _setup(client)
    await client.post(
        f"/videos/{video_id}/classify",
        json={"topic_mentions": [await _mention(topic_id)]},
    )
    new_mention = {**await _mention(topic_id), "sentiment": "bullish", "summary": "Yükseliş bekleniyor."}
    response = await client.post(
        f"/videos/{video_id}/classify",
        json={"topic_mentions": [new_mention]},
    )
    assert response.status_code == 201
    assert response.json()["mentions"][0]["sentiment"] == "bullish"


async def test_get_classification(client: AsyncClient):
    person_id, video_id, topic_id = await _setup(client)
    await client.post(
        f"/videos/{video_id}/classify",
        json={"topic_mentions": [await _mention(topic_id)]},
    )
    response = await client.get(f"/videos/{video_id}/classification")
    assert response.status_code == 200
    assert response.json()["video_id"] == video_id


async def test_get_classification_not_found_returns_404(client: AsyncClient):
    _person_id, video_id, _topic_id = await _setup(client)
    response = await client.get(f"/videos/{video_id}/classification")
    assert response.status_code == 404


async def test_get_prompt_template(client: AsyncClient):
    response = await client.get("/videos/classification/prompt")
    assert response.status_code == 200
    data = response.json()
    assert "system_prompt" in data
    assert "user_prompt_template" in data
