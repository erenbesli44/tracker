import pytest
from httpx import AsyncClient


async def test_create_person(client: AsyncClient):
    response = await client.post(
        "/persons/",
        json={"name": "Taner Genek", "platform": "youtube", "platform_handle": "@tanergenek"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Taner Genek"
    assert data["slug"] == "taner-genek"
    assert data["platform"] == "youtube"
    assert data["id"] is not None


async def test_create_person_duplicate_returns_409(client: AsyncClient):
    await client.post("/persons/", json={"name": "Taner Genek"})
    response = await client.post("/persons/", json={"name": "Taner Genek"})
    assert response.status_code == 409


async def test_list_persons(client: AsyncClient):
    await client.post("/persons/", json={"name": "Taner Genek"})
    await client.post("/persons/", json={"name": "Ahmet Yılmaz"})
    response = await client.get("/persons/")
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_get_person(client: AsyncClient):
    create_resp = await client.post("/persons/", json={"name": "Taner Genek"})
    person_id = create_resp.json()["id"]
    response = await client.get(f"/persons/{person_id}")
    assert response.status_code == 200
    assert response.json()["id"] == person_id


async def test_get_person_not_found(client: AsyncClient):
    response = await client.get("/persons/9999")
    assert response.status_code == 404


async def test_update_person(client: AsyncClient):
    create_resp = await client.post("/persons/", json={"name": "Taner Genek"})
    person_id = create_resp.json()["id"]
    response = await client.patch(
        f"/persons/{person_id}", json={"bio": "NCM Investment analisti"}
    )
    assert response.status_code == 200
    assert response.json()["bio"] == "NCM Investment analisti"
    assert response.json()["updated_at"] is not None
