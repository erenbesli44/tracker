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


async def test_inactive_channel_hidden_from_list_unless_requested(client: AsyncClient):
    active = await client.post(
        "/channels/", json={"name": "Aktif Kanal", "channel_handle": "@aktif"}
    )
    retired = await client.post(
        "/channels/", json={"name": "Eski Kanal", "channel_handle": "@eski"}
    )
    assert active.json()["is_active"] is True

    patched = await client.patch(f"/channels/{retired.json()['id']}", json={"is_active": False})
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False

    default_list = await client.get("/channels/")
    assert [c["name"] for c in default_list.json()] == ["Aktif Kanal"]

    full_list = await client.get("/channels/", params={"include_inactive": "true"})
    assert sorted(c["name"] for c in full_list.json()) == ["Aktif Kanal", "Eski Kanal"]

    # Direct lookup by id still works so the row can be reactivated later.
    by_id = await client.get(f"/channels/{retired.json()['id']}")
    assert by_id.status_code == 200
    assert by_id.json()["is_active"] is False
