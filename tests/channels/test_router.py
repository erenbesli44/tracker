from httpx import AsyncClient


async def test_create_channel(client: AsyncClient):
    response = await client.post(
        "/channels/",
        json={
            "name": "Atilla Yesilada",
            "channel_handle": "@atillayesilada",
            "platform": "youtube",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["name"] == "Atilla Yesilada"
    assert payload["slug"] == "atilla-yesilada"
    assert payload["channel_handle"] == "@atillayesilada"


async def test_list_channels(client: AsyncClient):
    await client.post("/channels/", json={"name": "Kanal A", "channel_handle": "@kanala"})
    await client.post("/channels/", json={"name": "Kanal B", "channel_handle": "@kanalb"})
    response = await client.get("/channels/")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_update_channel(client: AsyncClient):
    create = await client.post(
        "/channels/",
        json={"name": "Kanal C", "channel_handle": "@kanalc"},
    )
    channel_id = create.json()["id"]

    update = await client.patch(
        f"/channels/{channel_id}",
        json={"bio": "Makro yorumlar"},
    )
    assert update.status_code == 200
    assert update.json()["bio"] == "Makro yorumlar"
