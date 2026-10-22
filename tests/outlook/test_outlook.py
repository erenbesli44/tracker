

from httpx import AsyncClient
from sqlmodel import Session, select

from src.classification import outlook
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.videos.models import Video


def test_parse_drops_malformed_and_forces_none_not_explicit():
    payload = {
        "items": [
            {"mention_id": 1, "outlook": "up", "explicit": True, "quote": "80 liraya çıkar"},
            {"mention_id": 2, "outlook": "none", "explicit": True},  # none can't be explicit
            {"mention_id": 3, "outlook": "sideways", "explicit": True},  # invalid value
            {"mention_id": 99, "outlook": "down", "explicit": True},  # unknown id
            {"mention_id": "4", "outlook": "FLAT", "explicit": "yes"},  # explicit must be True
        ]
    }
    out = outlook.parse_outlook_items(payload, {1, 2, 3, 4})
    assert out[1]["outlook"] == "up" and out[1]["outlook_explicit"] is True
    assert out[1]["outlook_quote"] == "80 liraya çıkar"
    assert out[2] == {**out[2], "outlook": "none", "outlook_explicit": False}
    assert 3 not in out and 99 not in out
    assert out[4]["outlook"] == "flat" and out[4]["outlook_explicit"] is False


def test_parse_handles_missing_items():
    assert outlook.parse_outlook_items({"foo": 1}, {1}) == {}


def test_transcript_excerpt_uses_padded_window():
    segments = [
        {"start": 0.0, "text": "giriş"},
        {"start": 100.0, "text": "dolar yükselir"},
        {"start": 130.0, "text": "seksen lira"},
        {"start": 1000.0, "text": "alakasız"},
    ]
    text = outlook.transcript_excerpt(segments, "01:40", "02:00")
    assert "dolar yükselir" in text and "seksen lira" in text
    assert "giriş" not in text and "alakasız" not in text
    assert outlook.transcript_excerpt(segments, None, None) == ""
    assert outlook.transcript_excerpt(None, "01:40", "02:00") == ""


async def _make_video_with_mentions(client: AsyncClient) -> tuple[int, dict[str, int]]:
    topics = {t["slug"]: t["id"] for t in (await client.get("/topics/")).json()}
    channel = await client.post("/channels/", json={"name": "Test Kanal", "channel_handle": "@t"})
    video = await client.post(
        "/videos/",
        json={
            "channel_id": channel.json()["id"],
            "video_url": "https://www.youtube.com/watch?v=abc123def45",
        },
    )
    video_id = video.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript."})
    mentions = [
        {"topic_id": topics["doviz-kur"], "summary": "Döviz: kur 80 olur.", "sentiment": "bearish",
         "confidence": 0.9},
        {"topic_id": topics["ic-siyaset"], "summary": "Siyaset: seçim.", "sentiment": "neutral",
         "confidence": 0.8},
    ]
    res = await client.post(f"/videos/{video_id}/classify", json={"topic_mentions": mentions})
    assert res.status_code == 201
    return video_id, topics


async def test_classify_video_saves_only_instrument_mentions(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _make_video_with_mentions(client)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()

    seen = {}

    def fake_llm(**kwargs):
        seen.update(kwargs)
        return {"items": [{"mention_id": dolar.id, "outlook": "up", "explicit": True,
                           "quote": "kur 80 olur", "horizon": "yıl sonu"}]}

    monkeypatch.setattr(llm_service, "generate_outlook_json", fake_llm)
    video = session.get(Video, video_id)
    outlook.classify_video(session, video)

    # only the instrument mention was sent; the politics mention was not
    assert f"mention_id={dolar.id}" in seen["items_block"]
    assert "Dolar/TL" in seen["items_block"] and "seçim" not in seen["items_block"]

    session.expire_all()
    rows = {m.topic_id: m for m in session.exec(select(TopicMention)).all()}
    saved = rows[topics["doviz-kur"]]
    assert (saved.outlook, saved.outlook_explicit) == ("up", True)
    assert saved.outlook_horizon == "yıl sonu"
    assert saved.outlook_version == "outlook-v3"
    assert rows[topics["ic-siyaset"]].outlook is None

    body = (await client.get("/topics/doviz-kur/opinions")).json()
    entry = body["channel_opinions"][0]["entries"][0]
    assert entry["outlook"] == "up" and entry["outlook_explicit"] is True
    assert entry["outlook_quote"] == "kur 80 olur"


async def test_classify_video_dry_run_writes_nothing(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _make_video_with_mentions(client)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    monkeypatch.setattr(
        llm_service,
        "generate_outlook_json",
        lambda **_: {"items": [{"mention_id": dolar.id, "outlook": "down", "explicit": True}]},
    )
    res = outlook.classify_video(session, session.get(Video, video_id), dry_run=True)
    assert res[dolar.id]["outlook"] == "down"
    session.expire_all()
    assert session.get(TopicMention, dolar.id).outlook is None


async def test_safe_hook_swallows_llm_errors(client: AsyncClient, session: Session, monkeypatch):
    video_id, _ = await _make_video_with_mentions(client)

    def boom(**_):
        raise llm_service.LLMGenerationError("down")

    monkeypatch.setattr(llm_service, "generate_outlook_json", boom)
    outlook.classify_video_safely(session, session.get(Video, video_id))  # must not raise
    assert all(m.outlook is None for m in session.exec(select(TopicMention)).all())




async def test_backfill_endpoints_classify_pending_and_report(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _make_video_with_mentions(client)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    monkeypatch.setattr(
        llm_service,
        "generate_outlook_json",
        lambda **_: {"items": [{"mention_id": dolar.id, "outlook": "up", "explicit": True}]},
    )
    assert outlook.pending_video_ids(session, 10) == [video_id]

    # run synchronously against the test engine (the endpoint uses a background task)
    outlook.run_backfill(session.get_bind(), limit=10, workers=1)
    state = outlook.backfill_state()
    assert state["running"] is False and state["done"] == 1 and state["failed"] == 0

    session.expire_all()
    assert outlook.pending_video_ids(session, 10) == []
    status_body = (await client.get("/outlook/backfill")).json()
    assert status_body["coverage"] == {"instrument_mentions": 1, "classified": 1, "explicit": 1}


async def test_backfill_post_requires_api_key(client: AsyncClient):
    res = await client.post("/outlook/backfill", headers={"X-API-Key": "wrong"})
    assert res.status_code in (401, 403)
