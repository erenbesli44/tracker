"""Summary-only channels (Kayıt Dışı İktisat): a summary, never instrument mentions."""

from httpx import AsyncClient
from sqlmodel import Session, select

from src.classification.models import TopicMention
from src.ingestion import service as ingestion_service
from src.llm import service as llm_service
from src.persons import detector

VIDEO_URL = "https://www.youtube.com/watch?v=KayitDisi01"


def _analysis(**_kw) -> dict:
    seg = {"summary": "Dolar 50'yi görür.", "start_time": "13:10", "end_time": "14:00",
           "confidence": 0.8}
    return {
        "key_points": ["Seçim sonuçları"],
        "summary": {"short": "ABD ara seçimleri ve ekonomi."},
        "topic_segments": [{**seg, "subtopic": "doviz-kur"}, {**seg, "subtopic": "altin"}],
    }


def test_summary_only_channels_come_from_config():
    assert detector.is_summary_only_channel("@Kayitdisi")
    assert detector.is_summary_only_channel("kayitdisi")
    assert not detector.is_summary_only_channel("@MeseleEkonomi")
    assert not detector.is_summary_only_channel(None)


async def test_summary_only_channel_gets_summary_without_mentions(
    client: AsyncClient, session: Session, monkeypatch
):
    calls = []
    monkeypatch.setattr(ingestion_service, "_llm_generation_enabled", lambda: True)
    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)
    monkeypatch.setattr(
        llm_service, "generate_analysis_json", lambda **kw: calls.append(kw) or _analysis()
    )
    monkeypatch.setattr(
        llm_service, "generate_outlook_json", lambda **_kw: (_ for _ in ()).throw(AssertionError)
    )
    monkeypatch.setattr(
        ingestion_service.videos_service,
        "fetch_youtube_metadata",
        lambda _url: {
            "title": "Trump'ı Ekonomi mi Yenecek?",
            "author_name": "Kayıt Dışı İktisat",
            "author_url": "https://www.youtube.com/@Kayitdisi",
        },
    )
    body = {
        "video": {"video_url": VIDEO_URL},
        "transcript": {"raw_text": "Dolar, altın ve seçimler konuşuluyor.", "language": "tr"},
    }

    first = await client.post("/ingestions/youtube", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["actions"]["summary"] == "created"
    assert first.json()["actions"]["classification"] == "skipped"
    video_id = first.json()["video_id"]
    assert session.exec(select(TopicMention).where(TopicMention.video_id == video_id)).all() == []
    assert (await client.get(f"/videos/{video_id}/summary")).status_code == 200

    # Re-ingesting does not call the LLM again just because mentions are missing.
    second = await client.post("/ingestions/youtube", json=body)
    assert second.status_code == 200, second.text
    assert len(calls) == 1
