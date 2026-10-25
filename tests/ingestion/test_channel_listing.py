import pytest
from fastapi import HTTPException

from src.ingestion import service as ingestion_service

CHANNEL_ID = "UC1111111111111111111111"


def _entry(video_id: str, upload_date: str | None = None) -> dict:
    return {"id": video_id, "title": f"Title {video_id}", "upload_date": upload_date}


def _fake_yt_dlp(tabs: dict):
    calls: list[str] = []

    def fake_extract(url: str, _opts: dict):
        tab = url.rsplit("/", 1)[-1]
        calls.append(tab)
        result = tabs[tab]
        if isinstance(result, Exception):
            raise result
        return result

    return fake_extract, calls


def test_lists_uploads_then_streams_without_duplicates(monkeypatch):
    fake, calls = _fake_yt_dlp(
        {
            "videos": {
                "channel": "Test Channel",
                "entries": [_entry("AAAAAAAAAAA", "20261024"), _entry("BBBBBBBBBBB")],
            },
            "streams": {"entries": [_entry("LIVE0000001"), _entry("BBBBBBBBBBB")]},
        }
    )
    monkeypatch.setattr(ingestion_service.videos_service, "extract_info_with_yt_dlp", fake)

    info = ingestion_service._list_recent_channel_videos(CHANNEL_ID, 4)

    assert calls == ["videos", "streams"]
    assert [c.video_id for c in info.candidates] == ["AAAAAAAAAAA", "BBBBBBBBBBB", "LIVE0000001"]
    assert info.candidates[2].video_url == "https://www.youtube.com/watch?v=LIVE0000001"
    assert info.channel_name == "Test Channel"


def test_each_tab_is_capped_at_limit(monkeypatch):
    fake, _ = _fake_yt_dlp(
        {
            "videos": {"entries": [_entry(f"VIDEO000000{i}") for i in range(3)]},
            "streams": {"entries": [_entry(f"LIVE0000000{i}") for i in range(3)]},
        }
    )
    monkeypatch.setattr(ingestion_service.videos_service, "extract_info_with_yt_dlp", fake)

    info = ingestion_service._list_recent_channel_videos(CHANNEL_ID, 2)

    assert [c.video_id for c in info.candidates] == [
        "VIDEO0000000",
        "VIDEO0000001",
        "LIVE00000000",
        "LIVE00000001",
    ]


def test_channel_without_streams_tab_keeps_uploads(monkeypatch):
    fake, _ = _fake_yt_dlp(
        {
            "videos": {"entries": [_entry("AAAAAAAAAAA")]},
            "streams": Exception("This channel does not have a streams tab"),
        }
    )
    monkeypatch.setattr(ingestion_service.videos_service, "extract_info_with_yt_dlp", fake)

    info = ingestion_service._list_recent_channel_videos(CHANNEL_ID, 4)

    assert [c.video_id for c in info.candidates] == ["AAAAAAAAAAA"]


def test_videos_tab_failure_still_raises(monkeypatch):
    fake, _ = _fake_yt_dlp({"videos": Exception("blocked"), "streams": {"entries": []}})
    monkeypatch.setattr(ingestion_service.videos_service, "extract_info_with_yt_dlp", fake)

    with pytest.raises(HTTPException) as exc_info:
        ingestion_service._list_recent_channel_videos(CHANNEL_ID, 4)
    assert exc_info.value.status_code == 502
