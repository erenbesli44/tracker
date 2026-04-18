from datetime import datetime

from httpx import AsyncClient


async def _topic_id_by_slug(client: AsyncClient, slug: str) -> int:
    topics_resp = await client.get("/topics/")
    topics = topics_resp.json()
    return next(topic["id"] for topic in topics if topic["slug"] == slug)


async def test_ingest_youtube_full_create(client: AsyncClient):
    topic_id = await _topic_id_by_slug(client, "bist-turk-piyasalari")

    response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {
                "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
                "title": "Ingestion Test Video",
            },
            "transcript": {"raw_text": "Transkript metni.", "language": "tr"},
            "summary": {
                "short_summary": "Kisa ozet",
                "long_summary": "Detayli ozet",
                "highlights": ["Madde 1"],
            },
            "classification": {
                "topic_mentions": [
                    {
                        "topic_id": topic_id,
                        "summary": "BIST tarafinda baski suruyor.",
                        "sentiment": "bearish",
                        "confidence": 0.9,
                    }
                ]
            },
            "overwrite": {
                "transcript": True,
                "summary": True,
                "classification": True,
            },
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "stored"
    assert payload["person_id"] is None
    assert payload["channel_id"] >= 1
    assert payload["video_id"] >= 1
    assert payload["transcript_id"] >= 1
    assert payload["summary_id"] >= 1
    assert payload["classification_mentions"] == 1
    assert payload["actions"]["person"] == "skipped"
    assert payload["actions"]["channel"] == "created"
    assert payload["actions"]["video"] == "created"
    assert payload["actions"]["transcript"] == "created"
    assert payload["actions"]["summary"] == "created"
    assert payload["actions"]["classification"] == "created"

    persons = (await client.get("/persons/")).json()
    assert persons == []


async def test_ingest_youtube_skips_transcript_when_exists_and_no_overwrite(client: AsyncClient):
    person_resp = await client.post("/persons/", json={"name": "Taner Genek"})
    person_id = person_resp.json()["id"]
    video_resp = await client.post(
        "/videos/",
        json={"person_id": person_id, "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
    )
    video_id = video_resp.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Old transcript"})

    response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"id": person_id},
            "video": {"video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
            "transcript": {"raw_text": "New transcript"},
            "overwrite": {"transcript": False},
        },
    )
    # With overwrite=False, the existing transcript is kept and the pipeline continues.
    assert response.status_code == 200
    assert response.json()["actions"]["transcript"] == "skipped"

    # Confirm the original transcript was preserved.
    transcript_resp = await client.get(f"/videos/{video_id}/transcript")
    assert transcript_resp.json()["raw_text"] == "Old transcript"


async def test_ingest_youtube_overwrite_and_skip(client: AsyncClient):
    topic_id = await _topic_id_by_slug(client, "bist-turk-piyasalari")
    initial = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {"video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
            "transcript": {"raw_text": "Ilk transcript"},
            "summary": {"short_summary": "Ilk ozet"},
            "classification": {
                "topic_mentions": [{"topic_id": topic_id, "summary": "Ilk siniflandirma"}]
            },
            "overwrite": {"transcript": True, "summary": True, "classification": True},
        },
    )
    assert initial.status_code == 200

    response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {"video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
            "transcript": {"raw_text": "Guncel transcript"},
            "summary": {"short_summary": "Yeni ozet"},
            "classification": {
                "topic_mentions": [{"topic_id": topic_id, "summary": "Yeni siniflandirma"}]
            },
            "overwrite": {"transcript": True, "summary": False, "classification": False},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["actions"]["person"] == "skipped"
    assert payload["actions"]["channel"] == "reused"
    assert payload["actions"]["video"] == "reused"
    assert payload["actions"]["transcript"] == "updated"
    assert payload["actions"]["summary"] == "skipped"
    assert payload["actions"]["classification"] == "skipped"
    assert payload["classification_mentions"] == 1


async def test_ingest_youtube_invalid_topic_returns_422(client: AsyncClient):
    response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {"video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
            "transcript": {"raw_text": "Transkript"},
            "classification": {
                "topic_mentions": [{"topic_id": 999999, "summary": "Invalid topic id test"}]
            },
            "overwrite": {"transcript": True, "classification": True},
        },
    )
    assert response.status_code == 422


async def test_ingest_youtube_resolves_channel_from_metadata_without_person(
    client: AsyncClient, monkeypatch
):
    from src.ingestion import service as ingestion_service

    topic_id = await _topic_id_by_slug(client, "enflasyon")

    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Channel owned video",
            "author_name": "Atilla Yesilada",
            "author_url": "https://www.youtube.com/@atillayesilada",
        },
    )

    response = await client.post(
        "/ingestions/youtube",
        json={
            "video": {"video_url": "https://www.youtube.com/watch?v=DTyA0uQu__A"},
            "transcript": {"raw_text": "Transkript"},
            "classification": {
                "topic_mentions": [{"topic_id": topic_id, "summary": "Enflasyon baskisi"}]
            },
            "overwrite": {"transcript": True, "classification": True},
        },
    )
    assert response.status_code == 200
    payload = response.json()

    persons = (await client.get("/persons/")).json()
    assert persons == []
    assert payload["person_id"] is None
    assert payload["actions"]["person"] == "skipped"

    channels = (await client.get("/channels/")).json()
    channel = next(c for c in channels if c["id"] == payload["channel_id"])
    assert channel["name"] == "Atilla Yesilada"
    assert channel["channel_handle"] == "@atillayesilada"


async def test_ingest_youtube_relinks_existing_video_to_channel_owner(
    client: AsyncClient, monkeypatch
):
    from src.ingestion import service as ingestion_service

    # Existing wrong owner
    wrong_owner = await client.post("/persons/", json={"name": "Wrong Owner"})
    wrong_owner_id = wrong_owner.json()["id"]
    existing_video = await client.post(
        "/videos/",
        json={
            "person_id": wrong_owner_id,
            "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw&t=129s",
        },
    )
    assert existing_video.status_code == 201

    topic_id = await _topic_id_by_slug(client, "enflasyon")
    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Correct owner video",
            "author_name": "Taner Genek",
            "author_url": "https://www.youtube.com/@tanergenek",
        },
    )

    response = await client.post(
        "/ingestions/youtube",
        json={
            "video": {"video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"},
            "transcript": {"raw_text": "Transkript"},
            "classification": {
                "topic_mentions": [{"topic_id": topic_id, "summary": "Enflasyon baskisi"}]
            },
            "overwrite": {"transcript": True, "classification": True},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["channel_id"] >= 1
    assert payload["person_id"] is None
    assert payload["actions"]["person"] == "skipped"
    assert payload["actions"]["video"] == "reused"

    stored_video = await client.get(f"/videos/{payload['video_id']}")
    assert stored_video.status_code == 200
    assert stored_video.json()["channel_id"] == payload["channel_id"]
    assert stored_video.json()["person_id"] is None


async def test_ingest_youtube_channel_processes_last_n_and_skips_existing(
    client: AsyncClient, monkeypatch
):
    from src.ingestion import service as ingestion_service

    existing_person = await client.post("/persons/", json={"name": "Already Stored"})
    existing_person_id = existing_person.json()["id"]
    existing_video_id = "AAAAAAAAAAA"
    existing_video_url = f"https://www.youtube.com/watch?v={existing_video_id}"
    existing_video = await client.post(
        "/videos/",
        json={"person_id": existing_person_id, "video_url": existing_video_url},
    )
    assert existing_video.status_code == 201

    captured: dict[str, int] = {}
    candidates = [
        ingestion_service._ChannelVideoCandidate(
            video_id=existing_video_id,
            video_url=existing_video_url,
            title="Already Extracted",
            published_at=datetime(2026, 4, 10, 12, 0, 0),
        ),
        ingestion_service._ChannelVideoCandidate(
            video_id="BBBBBBBBBBB",
            video_url="https://www.youtube.com/watch?v=BBBBBBBBBBB",
            title="New video 1",
            published_at=datetime(2026, 4, 10, 13, 0, 0),
        ),
        ingestion_service._ChannelVideoCandidate(
            video_id="CCCCCCCCCCC",
            video_url="https://www.youtube.com/watch?v=CCCCCCCCCCC",
            title="New video 2",
            published_at=datetime(2026, 4, 10, 14, 0, 0),
        ),
    ]

    def fake_list_recent(channel_id: str, limit: int):
        captured["limit"] = limit
        assert channel_id == "UC1111111111111111111111"
        return ingestion_service._ChannelPlaylistInfo(
            candidates=candidates,
            channel_name="Test Channel",
            channel_handle="@testchannel",
        )

    monkeypatch.setattr(
        ingestion_service,
        "_resolve_youtube_channel_id",
        lambda _channel: "UC1111111111111111111111",
    )
    monkeypatch.setattr(ingestion_service, "_list_recent_channel_videos", fake_list_recent)
    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_transcript_from_youtube",
        lambda video_id, _langs=None: {
            "full_text": f"Transcript for {video_id}",
            "language": "tr",
            "segments": [{"start": 0, "duration": 1, "text": "dummy"}],
            "is_generated": False,
            "languages_tried": ["tr"],
        },
    )
    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Channel video",
            "author_name": "Taner Genek",
            "author_url": "https://www.youtube.com/@tanergenek",
        },
    )

    response = await client.post(
        "/ingestions/youtube/channel",
        json={"youtube_channel": "@tanergenek", "video_count": 3},
    )
    assert response.status_code == 200
    payload = response.json()
    assert captured["limit"] == 3
    assert payload["status"] == "completed"
    assert payload["youtube_channel_id"] == "UC1111111111111111111111"
    assert payload["requested_video_count"] == 3
    assert payload["videos_detected"] == 3
    assert payload["videos_ingested"] == 2
    assert payload["videos_skipped_existing"] == 1
    assert payload["videos_skipped_no_transcript"] == 0
    assert payload["errors_count"] == 0

    statuses = [item["status"] for item in payload["results"]]
    assert statuses.count("skipped_existing") == 1
    assert statuses.count("ingested") == 2

    videos = (await client.get("/videos/")).json()
    assert len(videos) == 3


async def test_ingest_youtube_channel_second_run_skips_already_extracted(
    client: AsyncClient, monkeypatch
):
    from src.ingestion import service as ingestion_service

    candidates = [
        ingestion_service._ChannelVideoCandidate(
            video_id="DDDDDDDDDDD",
            video_url="https://www.youtube.com/watch?v=DDDDDDDDDDD",
            title="First",
            published_at=datetime(2026, 4, 10, 12, 0, 0),
        ),
        ingestion_service._ChannelVideoCandidate(
            video_id="EEEEEEEEEEE",
            video_url="https://www.youtube.com/watch?v=EEEEEEEEEEE",
            title="Second",
            published_at=datetime(2026, 4, 10, 13, 0, 0),
        ),
    ]

    transcript_fetch_calls = {"count": 0}

    monkeypatch.setattr(
        ingestion_service,
        "_resolve_youtube_channel_id",
        lambda _channel: "UC2222222222222222222222",
    )
    monkeypatch.setattr(
        ingestion_service,
        "_list_recent_channel_videos",
        lambda _channel_id, _limit: ingestion_service._ChannelPlaylistInfo(
            candidates=candidates,
            channel_name="Atilla Yesilada",
            channel_handle="@atillayesilada",
        ),
    )

    def fake_fetch_transcript(video_id: str, _langs=None):
        transcript_fetch_calls["count"] += 1
        return {
            "full_text": f"Transcript for {video_id}",
            "language": "tr",
            "segments": [{"start": 0, "duration": 1, "text": "dummy"}],
            "is_generated": False,
            "languages_tried": ["tr"],
        }

    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_transcript_from_youtube",
        fake_fetch_transcript,
    )
    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Channel video",
            "author_name": "Atilla Yesilada",
            "author_url": "https://www.youtube.com/@atillayesilada",
        },
    )

    first_run = await client.post(
        "/ingestions/youtube/channel",
        json={"youtube_channel": "UC2222222222222222222222", "video_count": 2},
    )
    assert first_run.status_code == 200
    assert first_run.json()["videos_ingested"] == 2
    assert transcript_fetch_calls["count"] == 2

    second_run = await client.post(
        "/ingestions/youtube/channel",
        json={"youtube_channel": "UC2222222222222222222222", "video_count": 2},
    )
    assert second_run.status_code == 200
    second_payload = second_run.json()
    assert second_payload["videos_ingested"] == 0
    assert second_payload["videos_skipped_existing"] == 2
    assert transcript_fetch_calls["count"] == 2


async def test_ingest_youtube_auto_generates_summary_and_classification_for_new_video(
    client: AsyncClient, monkeypatch
):
    from src.ingestion import service as ingestion_service

    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Auto analytics test video",
            "author_name": "Atilla Yesilada",
            "author_url": "https://www.youtube.com/@atillayesilada",
        },
    )

    response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {
                "name": "Atilla Yesilada",
                "platform": "youtube",
                "platform_handle": "@atillayesilada",
            },
            "video": {"video_url": "https://www.youtube.com/watch?v=DTyA0uQu__A"},
            "transcript": {
                "raw_text": (
                    "Enflasyon yüksek seyrediyor. BIST tarafında dalgalanma var. "
                    "Dolar kurunda baskı devam ediyor."
                ),
                "language": "tr",
            },
            "overwrite": {"transcript": True, "summary": True, "classification": True},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["actions"]["video"] == "created"
    assert payload["actions"]["summary"] == "created"
    assert payload["actions"]["classification"] == "created"
    assert payload["summary_id"] is not None
    assert payload["classification_mentions"] >= 1

    summary_resp = await client.get(f"/videos/{payload['video_id']}/summary")
    assert summary_resp.status_code == 200
    assert summary_resp.json()["short_summary"]

    classification_resp = await client.get(f"/videos/{payload['video_id']}/classification")
    assert classification_resp.status_code == 200
    assert classification_resp.json()["total_mentions"] >= 1


async def test_ingest_youtube_does_not_regenerate_analytics_for_reused_video(
    client: AsyncClient, monkeypatch
):
    from src.ingestion import service as ingestion_service

    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Auto analytics replay test",
            "author_name": "Atilla Yesilada",
            "author_url": "https://www.youtube.com/@atillayesilada",
        },
    )

    first = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Atilla Yesilada", "platform_handle": "@atillayesilada"},
            "video": {"video_url": "https://www.youtube.com/watch?v=-CsKZj-_TFs"},
            "transcript": {
                "raw_text": "İlk transcript. Enflasyon ve faiz konuşuldu.",
                "language": "tr",
            },
            "overwrite": {"transcript": True, "summary": True, "classification": True},
        },
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["actions"]["summary"] == "created"
    assert first_payload["actions"]["classification"] == "created"

    second = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Atilla Yesilada", "platform_handle": "@atillayesilada"},
            "video": {"video_url": "https://www.youtube.com/watch?v=-CsKZj-_TFs"},
            "transcript": {"raw_text": "Güncel transcript.", "language": "tr"},
            "overwrite": {"transcript": True, "summary": True, "classification": True},
        },
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["video_id"] == first_payload["video_id"]
    assert second_payload["actions"]["video"] == "reused"
    assert second_payload["actions"]["summary"] == "skipped"
    assert second_payload["actions"]["classification"] == "skipped"
