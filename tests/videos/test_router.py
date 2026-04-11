from httpx import AsyncClient

from src.videos import service


async def _create_person(client: AsyncClient) -> int:
    resp = await client.post("/persons/", json={"name": "Taner Genek"})
    return resp.json()["id"]


async def test_create_video(client: AsyncClient):
    person_id = await _create_person(client)
    response = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
            "title": "Trader'ın Not Defteri #45",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["video_id"] == "bnk8WqphWMw"
    assert data["person_id"] == person_id


async def test_create_video_with_channel_only_owner(client: AsyncClient):
    channel_resp = await client.post(
        "/channels/",
        json={
            "name": "Channel Only Owner",
            "channel_handle": "@channelonly",
        },
    )
    channel_id = channel_resp.json()["id"]

    response = await client.post(
        "/videos/",
        json={
            "channel_id": channel_id,
            "video_url": "https://www.youtube.com/watch?v=chanonly001",
            "title": "Channel only video",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["channel_id"] == channel_id
    assert data["person_id"] is None


async def test_create_video_duplicate_url_returns_409(client: AsyncClient):
    person_id = await _create_person(client)
    url = "https://www.youtube.com/watch?v=bnk8WqphWMw"
    await client.post("/videos/", json={"person_id": person_id, "video_url": url})
    response = await client.post("/videos/", json={"person_id": person_id, "video_url": url})
    assert response.status_code == 409


async def test_create_video_duplicate_video_id_with_timestamp_query_returns_409(
    client: AsyncClient,
):
    person_id = await _create_person(client)
    await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
        },
    )
    response = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw&t=129s",
        },
    )
    assert response.status_code == 409


async def test_create_video_invalid_url_returns_422(client: AsyncClient):
    person_id = await _create_person(client)
    response = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://not-a-youtube-url.com/video"},
    )
    assert response.status_code == 422


async def test_list_videos_by_person_orders_by_published_at_desc_with_nulls_last(
    client: AsyncClient,
):
    person_id = await _create_person(client)

    older = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=oldvideo001",
            "published_at": "2026-04-01T10:00:00",
        },
    )
    newest = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=newvideo001",
            "published_at": "2026-04-03T10:00:00",
        },
    )
    undated = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=nodatevid01",
        },
    )

    assert older.status_code == 201
    assert newest.status_code == 201
    assert undated.status_code == 201

    response = await client.get(f"/videos/?person_id={person_id}")
    assert response.status_code == 200
    payload = response.json()

    assert [item["video_url"] for item in payload] == [
        "https://www.youtube.com/watch?v=newvideo001",
        "https://www.youtube.com/watch?v=oldvideo001",
        "https://www.youtube.com/watch?v=nodatevid01",
    ]


async def test_list_videos_by_channel_id(client: AsyncClient):
    person_id = await _create_person(client)
    first = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=chanlvid001",
            "published_at": "2026-04-01T10:00:00",
        },
    )
    second = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=chanlvid002",
            "published_at": "2026-04-03T10:00:00",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201

    channel_id = first.json()["channel_id"]
    assert channel_id is not None

    response = await client.get(f"/videos/?channel_id={channel_id}")
    assert response.status_code == 200
    payload = response.json()
    assert [item["video_url"] for item in payload] == [
        "https://www.youtube.com/watch?v=chanlvid002",
        "https://www.youtube.com/watch?v=chanlvid001",
    ]


async def test_add_transcript(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={
            "person_id": person_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
        },
    )
    video_id = video_resp.json()["id"]
    response = await client.post(
        f"/videos/{video_id}/transcript",
        json={"raw_text": "Test transkript içeriği.", "language": "tr"},
    )
    assert response.status_code == 201
    assert response.json()["video_id"] == video_id


async def test_add_transcript_duplicate_returns_409(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Test."})
    response = await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Test 2."})
    assert response.status_code == 409


async def test_get_video_with_transcript(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript."})
    response = await client.get(f"/videos/{video_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["transcript"] is not None
    assert data["transcript"]["video_id"] == video_id


async def test_get_transcript_returns_raw_text(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]
    await client.post(
        f"/videos/{video_id}/transcript",
        json={"raw_text": "Detayli transkript metni."},
    )

    response = await client.get(f"/videos/{video_id}/transcript")
    assert response.status_code == 200
    assert response.json()["raw_text"] == "Detayli transkript metni."


async def test_get_transcript_not_found_returns_404(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    response = await client.get(f"/videos/{video_id}/transcript")
    assert response.status_code == 404


async def test_fetch_transcript_from_youtube_creates_transcript(
    client: AsyncClient, monkeypatch
):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    def fake_fetch(video_id: str, languages: list[str] | None = None) -> dict:
        assert video_id == "bnk8WqphWMw"
        assert languages == ["tr", "en"]
        return {
            "full_text": "YouTube transcript text",
            "segments": [{"start": 0.0, "duration": 2.5, "text": "YouTube transcript text"}],
            "language": "tr",
            "is_generated": False,
            "languages_tried": ["tr", "en"],
        }

    monkeypatch.setattr(service, "fetch_transcript_from_youtube", fake_fetch)

    response = await client.post(
        f"/videos/{video_id}/transcript/fetch",
        json={"languages": ["tr", "en"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "youtube"
    assert payload["segment_count"] == 1
    assert payload["transcript"]["raw_text"] == "YouTube transcript text"


async def test_fetch_transcript_duplicate_without_overwrite_returns_409(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Manual transcript."})

    response = await client.post(f"/videos/{video_id}/transcript/fetch", json={})
    assert response.status_code == 409


async def test_fetch_transcript_with_overwrite_updates_existing(
    client: AsyncClient, monkeypatch
):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Old transcript."})

    monkeypatch.setattr(
        service,
        "fetch_transcript_from_youtube",
        lambda _video_id, _languages=None: {
            "full_text": "Fresh fetched transcript.",
            "segments": [{"start": 0.0, "duration": 1.0, "text": "Fresh fetched transcript."}],
            "language": "tr",
            "is_generated": True,
            "languages_tried": ["tr", "en"],
        },
    )

    response = await client.post(
        f"/videos/{video_id}/transcript/fetch",
        json={"overwrite_existing": True},
    )
    assert response.status_code == 200
    assert response.json()["transcript"]["raw_text"] == "Fresh fetched transcript."


async def test_fetch_transcript_provider_error_returns_502(client: AsyncClient, monkeypatch):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    def fake_fetch(_video_id: str, _languages=None) -> dict:
        raise service.YouTubeTranscriptFetchError(
            code="provider_error",
            detail="provider down",
        )

    monkeypatch.setattr(service, "fetch_transcript_from_youtube", fake_fetch)
    response = await client.post(f"/videos/{video_id}/transcript/fetch", json={})
    assert response.status_code == 502


async def test_fetch_transcript_unavailable_returns_422(client: AsyncClient, monkeypatch):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    def fake_fetch(_video_id: str, _languages=None) -> dict:
        raise service.YouTubeTranscriptFetchError(
            code="transcript_unavailable",
            detail="No transcript available.",
        )

    monkeypatch.setattr(service, "fetch_transcript_from_youtube", fake_fetch)
    response = await client.post(f"/videos/{video_id}/transcript/fetch", json={})
    assert response.status_code == 422


async def test_upsert_video_summary_create_then_get(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    create_resp = await client.post(
        f"/videos/{video_id}/summary",
        json={
            "short_summary": "Kisa ozet",
            "long_summary": "Detayli ozet metni",
            "highlights": ["Madde 1", "Madde 2"],
            "language": "tr",
            "source": "manual",
        },
    )
    assert create_resp.status_code == 200
    payload = create_resp.json()
    assert payload["video_id"] == video_id
    assert payload["short_summary"] == "Kisa ozet"
    assert payload["highlights"] == ["Madde 1", "Madde 2"]

    get_resp = await client.get(f"/videos/{video_id}/summary")
    assert get_resp.status_code == 200
    assert get_resp.json()["short_summary"] == "Kisa ozet"


async def test_get_video_summary_not_found_returns_404(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    response = await client.get(f"/videos/{video_id}/summary")
    assert response.status_code == 404


async def test_upsert_video_summary_updates_existing(client: AsyncClient):
    person_id = await _create_person(client)
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]

    await client.post(
        f"/videos/{video_id}/summary",
        json={"short_summary": "Ilk ozet", "highlights": ["A"]},
    )
    update_resp = await client.post(
        f"/videos/{video_id}/summary",
        json={"short_summary": "Guncel ozet", "highlights": ["B", "C"]},
    )
    assert update_resp.status_code == 200
    payload = update_resp.json()
    assert payload["short_summary"] == "Guncel ozet"
    assert payload["highlights"] == ["B", "C"]
    assert payload["updated_at"] is not None
