from httpx import AsyncClient


async def test_get_topic_tree(client: AsyncClient):
    response = await client.get("/topics/tree")
    assert response.status_code == 200
    tree = response.json()
    assert len(tree) >= 4  # ekonomi, siyaset, spor, teknoloji
    slugs = [t["slug"] for t in tree]
    assert "ekonomi" in slugs
    assert "siyaset" in slugs


async def test_topic_tree_has_sub_topics(client: AsyncClient):
    response = await client.get("/topics/tree")
    tree = response.json()
    ekonomi = next(t for t in tree if t["slug"] == "ekonomi")
    sub_slugs = [s["slug"] for s in ekonomi["sub_topics"]]
    assert "bist-turk-piyasalari" in sub_slugs
    assert "altin" in sub_slugs
    assert "gumus" in sub_slugs
    assert "kripto-paralar" in sub_slugs


async def test_list_topics(client: AsyncClient):
    response = await client.get("/topics/")
    assert response.status_code == 200
    assert len(response.json()) >= 4


async def test_create_topic(client: AsyncClient):
    response = await client.post(
        "/topics/",
        json={"name": "Test Konusu", "description": "Test açıklaması"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["slug"] == "test-konusu"


async def test_get_topic_not_found(client: AsyncClient):
    response = await client.get("/topics/9999")
    assert response.status_code == 404


async def test_topic_opinions_exclude_inactive_channels(client: AsyncClient):
    topics = (await client.get("/topics/")).json()
    topic = next(t for t in topics if t["slug"] == "bist-turk-piyasalari")
    mention = {
        "topic_id": topic["id"],
        "summary": "BIST100 yorumu.",
        "sentiment": "bearish",
        "confidence": 0.9,
    }

    channel_ids = {}
    for name, handle, yt_id in [
        ("Aktif Kanal", "@aktif", "aaa111bbb22"),
        ("Eski Kanal", "@eski", "ccc333ddd44"),
    ]:
        channel = await client.post("/channels/", json={"name": name, "channel_handle": handle})
        channel_ids[name] = channel.json()["id"]
        video = await client.post(
            "/videos/",
            json={
                "channel_id": channel.json()["id"],
                "video_url": f"https://www.youtube.com/watch?v={yt_id}",
            },
        )
        video_id = video.json()["id"]
        await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript."})
        classify = await client.post(
            f"/videos/{video_id}/classify", json={"topic_mentions": [mention]}
        )
        assert classify.status_code == 201

    before = await client.get(f"/topics/{topic['slug']}/opinions")
    assert before.json()["total_channels"] == 2

    await client.patch(f"/channels/{channel_ids['Eski Kanal']}", json={"is_active": False})

    after = await client.get(f"/topics/{topic['slug']}/opinions")
    assert after.status_code == 200
    assert after.json()["total_channels"] == 1
    assert after.json()["channel_opinions"][0]["channel_name"] == "Aktif Kanal"
