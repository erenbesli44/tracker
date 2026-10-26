from httpx import AsyncClient
from sqlmodel import Session, select

from src.classification import outlook, quote_clean
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.videos.models import Video
from tests.outlook.test_outlook import _make_video_with_mentions

RAW = "Ben 11.400'ün altında çok böyle uzun süre kalacağımızı açıkçası düşünmüyorum."
CLEAN = "Ben 11.400'ün altında uzun süre kalacağımızı düşünmüyorum."


def test_accepts_filler_removal():
    assert quote_clean.accept(RAW, CLEAN) == CLEAN


def test_rejects_changed_or_reformatted_numbers():
    assert quote_clean.accept(RAW, CLEAN.replace("11.400", "11.500")) is None
    raw = "4855 üzerinde kaldığı sürece yön yukarı"
    assert quote_clean.accept(raw, "4.855 üzerinde kaldığı sürece yön yukarı") is None


def test_rejects_dropped_negation_or_direction():
    flipped = "Ben 11.400'ün altında uzun süre kalacağımızı düşünüyorum."
    assert quote_clean.accept(RAW, flipped) is None
    raw = "Durum şu an için gümüş özelinde söz konusu değil."
    assert quote_clean.accept(raw, "Durum şu an için gümüş özelinde söz konusu.") is None
    raw = "4855 üzerinde kaldığı sürece yön yukarı potansiyel hedefler ekranda."
    assert quote_clean.accept(raw, "4855 üzerinde kaldığı sürece hedefler ekranda.") is None


def test_rejects_rewrites_with_new_words():
    raw = "petrol fiyatları brendte 114 dolar varile fırladı"
    assert quote_clean.accept(raw, "Petrol Brent'te 114 dolara varılı fırladı") is None
    assert quote_clean.accept(RAW, "") is None


def test_quote_context_centres_on_the_quote():
    excerpt = "giriş " * 200 + "ara seçimlerden sonra piyasa iyi olacak diyebilirim" + " son" * 200
    ctx = quote_clean.quote_context("Piyasa iyi olacak diyebilirim.", excerpt)
    assert "ara seçimlerden sonra" in ctx and len(ctx) < 900


async def test_classify_video_stores_clean_quote(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _make_video_with_mentions(client)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    monkeypatch.setattr(
        llm_service,
        "generate_outlook_json",
        lambda **_: {"items": [{"mention_id": dolar.id, "outlook": "up", "explicit": True,
                                "quote": RAW}]},
    )
    monkeypatch.setattr(
        llm_service,
        "generate_quote_clean_json",
        lambda **_: {"items": [{"id": dolar.id, "clean": CLEAN}]},
    )

    outlook.classify_video(session, session.get(Video, video_id))

    session.expire_all()
    saved = session.get(TopicMention, dolar.id)
    assert (saved.outlook_quote, saved.outlook_quote_clean) == (RAW, CLEAN)
    entry = (await client.get("/topics/doviz-kur/opinions")).json()["channel_opinions"][0][
        "entries"
    ][0]
    assert entry["outlook_quote_clean"] == CLEAN


async def test_rejected_edit_keeps_verbatim_and_llm_error_leaves_null(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _make_video_with_mentions(client)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    results = {dolar.id: {"outlook_quote": RAW}}
    items = [(dolar, "Dolar/TL", "")]

    monkeypatch.setattr(
        llm_service,
        "generate_quote_clean_json",
        lambda **_: {"items": [{"id": dolar.id, "clean": "Dolar 80 lira olacak."}]},
    )
    outlook.add_clean_quotes(results, items)
    assert results[dolar.id]["outlook_quote_clean"] == RAW

    def boom(**_):
        raise llm_service.LLMGenerationError("down")

    monkeypatch.setattr(llm_service, "generate_quote_clean_json", boom)
    outlook.add_clean_quotes(results, items)
    assert results[dolar.id]["outlook_quote_clean"] is None


async def test_clean_quote_backfill_fills_missing(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _make_video_with_mentions(client)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    dolar.outlook, dolar.outlook_explicit, dolar.outlook_quote = "up", True, RAW
    session.add(dolar)
    session.commit()
    monkeypatch.setattr(
        llm_service,
        "generate_quote_clean_json",
        lambda **_: {"items": [{"id": dolar.id, "clean": CLEAN}]},
    )

    outlook.run_clean_quote_backfill(session.get_bind(), limit=10)

    state = outlook.clean_quote_state()
    assert state["running"] is False and state["done"] == 1 and state["failed"] == 0
    session.expire_all()
    assert session.get(TopicMention, dolar.id).outlook_quote_clean == CLEAN
    body = (await client.get("/outlook/clean-quotes")).json()
    assert body["coverage"] == {"quotes": 1, "cleaned": 1}
