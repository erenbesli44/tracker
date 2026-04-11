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
    assert "borsa-istanbul" in sub_slugs
    assert "altin" in sub_slugs
    assert "gumus" in sub_slugs
    assert "bitcoin-kripto" in sub_slugs


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
