from httpx import AsyncClient

from src.ingestion import service as ingestion_service

LIVE_ID = "LIVELIVE001"
UPLOAD_ID = "UPLOAD00001"


def _candidate(video_id: str) -> ingestion_service._ChannelVideoCandidate:
    return ingestion_service._ChannelVideoCandidate(
        video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        title=f"Video {video_id}",
        published_at=None,
    )


async def test_channel_run_skips_live_streams(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(
        ingestion_service,
        "_resolve_youtube_channel_id",
        lambda _channel: "UC1111111111111111111111",
    )
    monkeypatch.setattr(
        ingestion_service,
        "_list_recent_channel_videos",
        lambda _channel_id, _limit: ingestion_service._ChannelPlaylistInfo(
            candidates=[_candidate(LIVE_ID), _candidate(UPLOAD_ID)],
            channel_name="Test Channel",
            channel_handle="@testchannel",
        ),
    )
    monkeypatch.setattr(ingestion_service, "_list_channel_stream_ids", lambda _cid: {LIVE_ID})
    fetched: list[str] = []

    def fake_transcript(video_id, _langs=None):
        fetched.append(video_id)
        return {
            "full_text": f"Transcript for {video_id}",
            "language": "tr",
            "segments": [{"start": 0, "duration": 1, "text": "dummy"}],
            "is_generated": False,
            "languages_tried": ["tr"],
        }

    monkeypatch.setattr(
        ingestion_service.videos_service, "fetch_transcript_from_youtube", fake_transcript
    )

    response = await client.post(
        "/ingestions/youtube/channel",
        json={"youtube_channel": "@testchannel", "video_count": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["videos_ingested"] == 1
    assert payload["videos_skipped_live"] == 1
    assert {r["youtube_video_id"]: r["status"] for r in payload["results"]} == {
        LIVE_ID: "skipped_live",
        UPLOAD_ID: "ingested",
    }
    assert fetched == [UPLOAD_ID]
    stored = [v["video_id"] for v in (await client.get("/videos/")).json()["items"]]
    assert stored == [UPLOAD_ID]


async def test_manual_ingest_rejects_live_stream(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(ingestion_service.videos_service, "fetch_is_live_content", lambda _u: True)

    response = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {"video_url": f"https://www.youtube.com/watch?v={LIVE_ID}"},
            "transcript": {"raw_text": "metin", "language": "tr"},
        },
    )

    assert response.status_code == 422
    assert (await client.get("/videos/")).json()["items"] == []


async def test_delete_video_removes_it_and_its_transcript(client: AsyncClient):
    created = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {"video_url": f"https://www.youtube.com/watch?v={UPLOAD_ID}"},
            "transcript": {"raw_text": "metin", "language": "tr"},
        },
    )
    assert created.status_code in (200, 201)
    video_id = created.json()["video_id"]

    response = await client.delete(f"/videos/{video_id}")

    assert response.status_code == 204
    assert (await client.get(f"/videos/{video_id}")).status_code == 404
    assert (await client.delete(f"/videos/{video_id}")).status_code == 404


async def test_delete_video_respects_foreign_keys(client: AsyncClient, session):
    """Postgres enforces foreign keys; child rows must go before the video row."""
    from sqlalchemy import text

    created = await client.post(
        "/ingestions/youtube",
        json={
            "person": {"name": "Taner Genek"},
            "video": {"video_url": f"https://www.youtube.com/watch?v={UPLOAD_ID}"},
            "transcript": {"raw_text": "metin", "language": "tr"},
            "summary": {"short_summary": "kısa", "long_summary": "uzun"},
        },
    )
    assert created.status_code in (200, 201), created.text
    video_id = created.json()["video_id"]
    session.exec(text("PRAGMA foreign_keys=ON"))

    response = await client.delete(f"/videos/{video_id}")

    assert response.status_code == 204
    assert (await client.get(f"/videos/{video_id}")).status_code == 404
