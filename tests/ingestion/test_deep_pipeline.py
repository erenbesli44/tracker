from httpx import AsyncClient


VIDEO_URL = "https://www.youtube.com/watch?v=DTyA0uQu__A"


async def _topic_id_by_slug(client: AsyncClient, slug: str) -> int:
    topics = (await client.get("/topics/")).json()
    return next(topic["id"] for topic in topics if topic["slug"] == slug)


async def test_deep_pipeline_for_specific_video(client: AsyncClient, monkeypatch):
    from src.ingestion import service as ingestion_service

    enflasyon_topic_id = await _topic_id_by_slug(client, "enflasyon")
    borsa_topic_id = await _topic_id_by_slug(client, "borsa-istanbul")

    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_oembed_metadata",
        lambda _url: {
            "title": "Makro Görünüm ve Piyasalar",
            "author_name": "Atilla Yesilada",
            "author_url": "https://www.youtube.com/@atillayesilada",
        },
    )

    initial_response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {
                "name": "Atilla Yesilada",
                "platform": "youtube",
                "platform_handle": "@atillayesilada",
            },
            "video": {"video_url": VIDEO_URL},
            "transcript": {
                "raw_text": (
                    "Enflasyonun etkileri, BIST görünümü, kur baskısı ve "
                    "yatırımcı beklentileri detaylı şekilde tartışılıyor."
                ),
                "language": "tr",
            },
            "summary": {
                "short_summary": "Makro riskler ve piyasa oynaklığı öne çıkıyor.",
                "long_summary": "Enflasyon, faiz ve kur dinamikleri birlikte ele alınıyor.",
                "highlights": ["Enflasyon baskısı", "BIST dalgalanması"],
                "language": "tr",
                "source": "manual",
            },
            "classification": {
                "source": "manual",
                "topic_mentions": [
                    {
                        "topic_id": enflasyon_topic_id,
                        "summary": "Enflasyon tarafında yukarı yönlü risklerin sürdüğü belirtildi.",
                        "sentiment": "bearish",
                        "confidence": 0.92,
                    },
                    {
                        "topic_id": borsa_topic_id,
                        "summary": "BIST'te kısa vadede dalgalı bir seyir bekleniyor.",
                        "sentiment": "neutral",
                        "confidence": 0.85,
                    },
                ],
            },
            "overwrite": {"transcript": True, "summary": True, "classification": True},
        },
    )
    assert initial_response.status_code == 200
    initial_payload = initial_response.json()
    assert initial_payload["status"] == "stored"
    assert initial_payload["video_id"] >= 1
    assert initial_payload["transcript_id"] >= 1
    assert initial_payload["summary_id"] >= 1
    assert initial_payload["classification_mentions"] == 2
    assert initial_payload["person_id"] is None
    assert initial_payload["actions"]["person"] == "skipped"
    assert initial_payload["actions"]["video"] == "created"
    assert initial_payload["actions"]["transcript"] == "created"
    assert initial_payload["actions"]["summary"] == "created"
    assert initial_payload["actions"]["classification"] == "created"

    video_id = initial_payload["video_id"]
    channel_id = initial_payload["channel_id"]

    persons = (await client.get("/persons/")).json()
    assert persons == []

    channels = (await client.get("/channels/")).json()
    assert len(channels) == 1
    assert channels[0]["id"] == channel_id
    assert channels[0]["name"] == "Atilla Yesilada"
    assert channels[0]["channel_handle"] == "@atillayesilada"

    video_response = await client.get(f"/videos/{video_id}")
    assert video_response.status_code == 200
    video_payload = video_response.json()
    assert video_payload["video_id"] == "DTyA0uQu__A"
    assert video_payload["video_url"] == VIDEO_URL
    assert video_payload["channel_id"] == channel_id
    assert video_payload["person_id"] is None

    transcript_response = await client.get(f"/videos/{video_id}/transcript")
    assert transcript_response.status_code == 200
    transcript_payload = transcript_response.json()
    assert transcript_payload["video_id"] == video_id
    assert transcript_payload["language"] == "tr"
    assert "Enflasyonun etkileri" in transcript_payload["raw_text"]

    summary_response = await client.get(f"/videos/{video_id}/summary")
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["video_id"] == video_id
    assert summary_payload["short_summary"] == "Makro riskler ve piyasa oynaklığı öne çıkıyor."
    assert summary_payload["highlights"] == ["Enflasyon baskısı", "BIST dalgalanması"]

    classification_response = await client.get(f"/videos/{video_id}/classification")
    assert classification_response.status_code == 200
    classification_payload = classification_response.json()
    assert classification_payload["video_id"] == video_id
    assert classification_payload["total_mentions"] == 2
    assert {item["topic_id"] for item in classification_payload["mentions"]} == {
        enflasyon_topic_id,
        borsa_topic_id,
    }

    channel_overview_response = await client.get(f"/channels/{channel_id}/topics/overview")
    assert channel_overview_response.status_code == 200
    channel_overview_payload = channel_overview_response.json()
    assert channel_overview_payload["channel_id"] == channel_id
    assert channel_overview_payload["person_id"] is None
    assert len(channel_overview_payload["topics"]) == 2

    channel_timeline_response = await client.get(
        f"/channels/{channel_id}/topics/{enflasyon_topic_id}/timeline"
    )
    assert channel_timeline_response.status_code == 200
    channel_timeline_payload = channel_timeline_response.json()
    assert channel_timeline_payload["channel_id"] == channel_id
    assert channel_timeline_payload["person_id"] is None
    assert channel_timeline_payload["total"] == 1
    assert channel_timeline_payload["entries"][0]["video_id"] == video_id

    replay_response = await client.post(
        "/ingestions/youtube",
        json={
            "video": {"video_url": VIDEO_URL},
            "transcript": {
                "raw_text": "Güncel transcript metni.",
                "language": "tr",
            },
            "summary": {
                "short_summary": "Güncel kısa özet",
                "long_summary": "Güncel uzun özet",
                "highlights": ["Güncel vurgu"],
            },
            "classification": {
                "topic_mentions": [
                    {
                        "topic_id": enflasyon_topic_id,
                        "summary": "Enflasyonda görünüm bozuluyor.",
                        "sentiment": "bearish",
                        "confidence": 0.95,
                    }
                ]
            },
            "overwrite": {"transcript": True, "summary": True, "classification": True},
        },
    )
    assert replay_response.status_code == 200
    replay_payload = replay_response.json()
    assert replay_payload["video_id"] == video_id
    assert replay_payload["person_id"] is None
    assert replay_payload["actions"]["person"] == "skipped"
    assert replay_payload["actions"]["video"] == "reused"
    assert replay_payload["actions"]["transcript"] == "updated"
    assert replay_payload["actions"]["summary"] == "updated"
    assert replay_payload["actions"]["classification"] == "replaced"
    assert replay_payload["classification_mentions"] == 1

    replay_summary_response = await client.get(f"/videos/{video_id}/summary")
    assert replay_summary_response.status_code == 200
    assert replay_summary_response.json()["short_summary"] == "Güncel kısa özet"

    replay_classification_response = await client.get(f"/videos/{video_id}/classification")
    assert replay_classification_response.status_code == 200
    replay_classification_payload = replay_classification_response.json()
    assert replay_classification_payload["total_mentions"] == 1
    assert replay_classification_payload["mentions"][0]["topic_id"] == enflasyon_topic_id
