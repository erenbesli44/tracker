from httpx import AsyncClient

from src.ingestion import service as ingestion_service
from src.jobs.youtube_watch.config import load_watch_config

SEANS_ID = "SEANSBAS001"
UPLOAD_ID = "UPLOAD00002"


def _candidate(video_id: str, title: str) -> ingestion_service._ChannelVideoCandidate:
    return ingestion_service._ChannelVideoCandidate(
        video_id=video_id,
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        title=title,
        published_at=None,
    )


async def test_channel_run_skips_excluded_titles(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(
        ingestion_service,
        "_resolve_youtube_channel_id",
        lambda _channel: "UC2222222222222222222222",
    )
    monkeypatch.setattr(
        ingestion_service,
        "_list_recent_channel_videos",
        lambda _channel_id, _limit: ingestion_service._ChannelPlaylistInfo(
            candidates=[
                _candidate(SEANS_ID, "SEANS BAŞLARKEN 10.11.2026"),
                _candidate(UPLOAD_ID, "Piyasalarda Dengeler Değişiyor mu?"),
            ],
            channel_name="NCM Investment TR",
            channel_handle="@ncminvest-tr",
        ),
    )
    monkeypatch.setattr(ingestion_service, "_list_channel_stream_ids", lambda _cid: set())
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
        json={
            "youtube_channel": "@ncminvest-tr",
            "video_count": 2,
            "exclude_title_contains": ["Seans Başlarken"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["videos_ingested"] == 1
    assert payload["videos_skipped_excluded"] == 1
    assert {r["youtube_video_id"]: r["status"] for r in payload["results"]} == {
        SEANS_ID: "skipped_excluded",
        UPLOAD_ID: "ingested",
    }
    assert fetched == [UPLOAD_ID]
    stored = [v["video_id"] for v in (await client.get("/videos/")).json()["items"]]
    assert stored == [UPLOAD_ID]


def test_watch_config_excludes_ncm_seans_baslarken():
    config = load_watch_config("config/watched_channels.yaml")
    ncm = next(c for c in config.channels if c.handle == "@ncminvest-tr")
    assert ncm.exclude_title_contains == ["Seans Başlarken"]
    others = [c for c in config.channels if c.handle != "@ncminvest-tr"]
    assert all(c.exclude_title_contains == [] for c in others)
