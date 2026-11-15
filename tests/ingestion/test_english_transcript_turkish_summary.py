"""English-transcript channels (Tom Lee, Joseph Carlson): the summary is still Turkish."""

from httpx import AsyncClient

from src.ingestion import service as ingestion_service
from src.llm import service as llm_service
from src.persons import detector

VIDEO_URL = "https://www.youtube.com/watch?v=FundstratT1"


def _analysis(**_kw) -> dict:
    return {
        "key_points": ["Nasdaq'ta %15 ralli beklentisi"],
        "summary": {"short": "Tom Lee, satışlar azaldıkça Nasdaq'ta ralli bekliyor."},
        "topic_segments": [],
    }


def test_us_channels_have_default_persons():
    assert detector.detect_channel_default("@FundstratCapital") == "tom-lee"
    assert detector.detect_channel_default("@JosephCarlsonAfterHours") == "joseph-carlson"


async def test_english_transcript_gets_turkish_summary(client: AsyncClient, monkeypatch):
    calls = []
    monkeypatch.setattr(ingestion_service, "_llm_generation_enabled", lambda: True)
    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)
    monkeypatch.setattr(
        llm_service, "generate_analysis_json", lambda **kw: calls.append(kw) or _analysis()
    )
    monkeypatch.setattr(llm_service, "generate_outlook_json", lambda **_kw: {"items": []})
    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "TOM LEE: AI massacre is creating an attractive opportunity",
            "author_name": "Fundstrat Capital",
            "author_url": "https://www.youtube.com/@FundstratCapital",
        },
    )
    body = {
        "video": {"video_url": VIDEO_URL},
        "transcript": {"raw_text": "I think a 15% rally will come underway.", "language": "en"},
    }

    res = await client.post("/ingestions/youtube", json=body)
    assert res.status_code == 200, res.text
    assert calls and calls[0]["output_language"] == "tr"
    summary = (await client.get(f"/videos/{res.json()['video_id']}/summary")).json()
    assert summary["language"] == "tr"
