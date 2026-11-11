

from httpx import AsyncClient
from sqlmodel import Session, select

from src.classification import outlook
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.llm.prompts import OUTLOOK_PROMPT_VERSION
from src.videos.models import Video


def test_parse_drops_malformed_and_forces_none_not_explicit():
    payload = {
        "items": [
            {"mention_id": 1, "outlook": "up", "explicit": True, "quote": "80 liraya çıkar",
             "time": "future", "own_view": True},
            {"mention_id": 2, "outlook": "none", "explicit": True},  # none can't be explicit
            {"mention_id": 3, "outlook": "sideways", "explicit": True},  # invalid value
            {"mention_id": 99, "outlook": "down", "explicit": True},  # unknown id
            {"mention_id": "4", "outlook": "FLAT", "explicit": "yes",  # explicit must be True
             "time": "future", "own_view": True, "quote": "yatay"},
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


def test_parse_current_or_past_state_is_never_a_direction():
    future = {"explicit": True, "own_view": True, "quote": "q"}
    payload = {
        "items": [
            {"mention_id": 1, "outlook": "down", "time": "present", **future},
            {"mention_id": 2, "outlook": "up", "time": "past", **future},
            {"mention_id": 3, "outlook": "down", **future},  # time missing
            {"mention_id": 4, "outlook": "up", "time": "soon", **future},  # invalid time
            {"mention_id": 5, "outlook": "down", "time": " Future ", **future},
        ]
    }
    out = outlook.parse_outlook_items(payload, {1, 2, 3, 4, 5})
    for mid in (1, 2, 3, 4):
        assert (out[mid]["outlook"], out[mid]["outlook_explicit"]) == ("none", False)
    assert (out[5]["outlook"], out[5]["outlook_explicit"]) == ("down", True)


def test_parse_explicit_needs_own_view_and_quote():
    base = {"outlook": "up", "explicit": True, "time": "future"}
    payload = {
        "items": [
            {"mention_id": 1, **base, "own_view": False, "quote": "q"},  # reported view
            {"mention_id": 2, **base, "quote": "q"},  # own_view missing
            {"mention_id": 3, **base, "own_view": "true", "quote": "q"},  # not a bool
            {"mention_id": 4, **base, "own_view": True, "quote": "  "},  # no quote
            {"mention_id": 5, **base, "own_view": True, "quote": "q"},
        ]
    }
    out = outlook.parse_outlook_items(payload, {1, 2, 3, 4, 5})
    for mid in (1, 2, 3, 4):
        # a future direction is kept, but never shown as the speaker's explicit view
        assert (out[mid]["outlook"], out[mid]["outlook_explicit"]) == ("up", False)
    assert out[4]["outlook_quote"] is None
    assert out[5]["outlook_explicit"] is True


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
                           "quote": "kur 80 olur", "horizon": "yıl sonu",
                           "time": "future", "own_view": True}]}

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
    assert saved.outlook_version == "outlook-v4"
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
        lambda **_: {"items": [{"mention_id": dolar.id, "outlook": "down", "explicit": True,
                                "time": "future", "own_view": True, "quote": "kur düşer"}]},
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
        lambda **_: {"items": [{"mention_id": dolar.id, "outlook": "up", "explicit": True,
                                "time": "future", "own_view": True, "quote": "kur 80 olur"}]},
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


async def _add_video(client: AsyncClient, channel_id: int, url_id: str, mentions: list) -> int:
    video = await client.post(
        "/videos/",
        json={"channel_id": channel_id, "video_url": f"https://www.youtube.com/watch?v={url_id}"},
    )
    video_id = video.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript."})
    res = await client.post(f"/videos/{video_id}/classify", json={"topic_mentions": mentions})
    assert res.status_code == 201
    return video_id


def _fake_outlook_for_block(seen: list, **fields):
    def fake(**kwargs):
        seen.append(kwargs["items_block"])
        ids = [int(x.split("]")[0]) for x in kwargs["items_block"].split("[mention_id=")[1:]]
        return {"items": [{"mention_id": i, **fields} for i in ids]}

    return fake


async def test_prompt_version_bump_requeues_whole_videos_and_keeps_current_ones(
    client: AsyncClient, session: Session, monkeypatch
):
    video_a, topics = await _make_video_with_mentions(client)
    dolar_a = session.exec(
        select(TopicMention).where(TopicMention.video_id == video_a)
        .where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    dolar_a.outlook, dolar_a.outlook_explicit, dolar_a.outlook_version = "down", True, "outlook-v3"
    session.add(dolar_a)
    # video B: one instrument mention already on the current version, one still on v3
    video_b = await _add_video(
        client, session.get(Video, video_a).channel_id, "zzz999yyy88",
        [{"topic_id": topics["doviz-kur"], "summary": "Döviz: kur.", "confidence": 0.9},
         {"topic_id": topics["altin"], "summary": "Altın: yükselir.", "confidence": 0.9}],
    )
    b = {m.topic_id: m for m in session.exec(
        select(TopicMention).where(TopicMention.video_id == video_b)).all()}
    dolar_b, altin_b = b[topics["doviz-kur"]], b[topics["altin"]]
    dolar_b.outlook, dolar_b.outlook_explicit = "up", True
    dolar_b.outlook_version, dolar_b.outlook_quote = OUTLOOK_PROMPT_VERSION, "kur çıkar"
    altin_b.outlook, altin_b.outlook_version = "up", "outlook-v3"
    session.add_all([dolar_b, altin_b])
    session.commit()
    dolar_b_id, altin_b_id = dolar_b.id, altin_b.id

    # A: everything on an older prompt -> whole video; B: only the stale mention
    assert sorted(outlook.pending_video_ids(session, 10)) == sorted([video_a, video_b])
    assert outlook.partially_pending_mention_ids(session, video_a) is None
    assert outlook.partially_pending_mention_ids(session, video_b) == {altin_b_id}

    seen: list[str] = []
    monkeypatch.setattr(llm_service, "generate_outlook_json", _fake_outlook_for_block(
        seen, outlook="down", explicit=True, time="present", own_view=True,
        quote="baskı sürüyor",
    ))
    outlook.run_backfill(session.get_bind(), limit=10, workers=1)
    state = outlook.backfill_state()
    assert (state["done"], state["failed"], state["missing_contract"]) == (2, 0, 0)

    blocks = "\n".join(seen)
    assert f"mention_id={dolar_a.id}" in blocks and f"mention_id={altin_b_id}" in blocks
    assert f"mention_id={dolar_b_id}" not in blocks  # current-version view not re-sent
    session.expire_all()
    for mid in (dolar_a.id, altin_b_id):
        saved = session.get(TopicMention, mid)
        assert (saved.outlook, saved.outlook_explicit) == ("none", False)
        assert saved.outlook_version == OUTLOOK_PROMPT_VERSION
    kept = session.get(TopicMention, dolar_b_id)
    assert (kept.outlook, kept.outlook_explicit, kept.outlook_quote) == ("up", True, "kur çıkar")
    assert outlook.pending_video_ids(session, 10) == []


def test_parse_logs_and_counts_items_missing_the_contract(caplog):
    payload = {
        "items": [
            {"mention_id": 1, "outlook": "up", "explicit": True, "quote": "q"},  # no time/own
            {"mention_id": 2, "outlook": "up", "time": "future", "own_view": "yes"},
            {"mention_id": 3, "outlook": "up", "time": "soon", "own_view": True},
            {"mention_id": 4, "outlook": "up", "time": "future", "own_view": False},  # valid
            {"mention_id": 99, "outlook": "up"},  # unknown id: ignored
        ]
    }
    stats: dict[str, int] = {}
    with caplog.at_level("WARNING", logger="src.classification.outlook"):
        outlook.parse_outlook_items(payload, {1, 2, 3, 4}, stats)
        outlook.parse_outlook_items({"items": payload["items"][3:4]}, {4}, stats)
    warnings = [r for r in caplog.records if "missing time/own_view" in r.getMessage()]
    assert len(warnings) == 1  # one per payload, none for a clean payload
    assert "3 item(s)" in warnings[0].getMessage() and "[1, 2, 3]" in warnings[0].getMessage()
    assert stats == {"missing_contract": 3}


async def test_backfill_reports_missing_contract(
    client: AsyncClient, session: Session, monkeypatch
):
    await _make_video_with_mentions(client)
    monkeypatch.setattr(llm_service, "generate_outlook_json", _fake_outlook_for_block(
        [], outlook="up", explicit=True, quote="kur 80 olur",  # v3-shaped: no time/own_view
    ))
    outlook.run_backfill(session.get_bind(), limit=10, workers=1)
    assert outlook.backfill_state()["missing_contract"] == 1
    body = (await client.get("/outlook/backfill")).json()
    assert body["run"]["missing_contract"] == 1
    assert body["coverage"]["explicit"] == 0


def _talk(n: int, start: float = 0.0) -> list[dict]:
    return [
        {"start": start + i * 4.0, "text": f"piyasada bugün olanları anlatıyorum {i:03d}"}
        for i in range(n)
    ]


def test_long_window_keeps_the_view_stated_far_from_its_start():
    # A mention that spans the whole video: the view comes ten minutes in (Bora Özkent,
    # 11 Nov 2026). The head-only excerpt never showed it to the model.
    segments = _talk(160)
    segments[150]["text"] = "an itibariyle ben hala yıl sonuna kadar boğa bekliyorum"
    text = outlook.transcript_excerpt(segments, "00:54", "11:15", "amerikan-piyasalari")
    assert "yıl sonuna kadar boğa bekliyorum" in text
    assert text.startswith("piyasada bugün olanları anlatıyorum 003")  # head is kept
    assert "…" in text and len(text) <= 3500


def test_long_window_without_cues_keeps_its_head():
    segments = _talk(160)
    text = outlook.transcript_excerpt(segments, "00:54", "11:15", "altin")
    assert "…" not in text
    assert text.startswith("piyasada bugün olanları anlatıyorum 003")
    assert "anlatıyorum 150" not in text


def test_missed_instruments_finds_a_short_view_without_a_mention():
    from src.classification import view_cues

    # Atilla Yeşilada, 11 Nov 2026: 25 seconds on altın at the end of a BIST video.
    segments = _talk(300) + [
        {"start": 1296.0, "text": "İnanmayacağın bir şey söyleyeceğim. Altın"},
        {"start": 1298.0, "text": "bu ortamda altın spot altın."},
        {"start": 1307.0, "text": "sabah baktığımda 4100 doların altına kaymıştı ons altın."},
        {"start": 1312.0, "text": "Evet. 4.500 dolara kadar çıkış bekliyorum"},
    ]
    assert view_cues.missed_instruments(segments, {"bist-turk-piyasalari"}) == {"altin": 1296.0}
    assert view_cues.missed_instruments(segments, {"bist-turk-piyasalari", "altin"}) == {}


def test_missed_instruments_ignores_teaser_units_and_plain_mentions():
    from src.classification import view_cues

    segments = [
        # opening teaser: the same words, but inside the first 90 seconds
        {"start": 16.0, "text": "Altın bu ortamda altın spot altın çıkış bekliyorum"},
        # "altına / altında" = below, "dolar" = a price unit: not instrument words
        {"start": 400.0, "text": "endeks 11.600'ün altına inerse satış bekliyorum 90 dolar"},
        # the instrument is named, nobody commits to anything
        {"start": 700.0, "text": "gümüş dün yüzde dört düştü"},
    ]
    assert view_cues.missed_instruments(segments, set()) == {}


def test_excerpt_appends_a_recap_of_the_instrument_outside_the_window():
    segments = _talk(300)
    segments[40]["text"] = "ons altın dün yüzde iki düştü"
    segments[280]["text"] = "özetle ons altın için 4.500 dolara kadar çıkış bekliyorum"
    text = outlook.transcript_excerpt(segments, "02:30", "03:30", "altin")
    assert "ons altın dün yüzde iki düştü" in text
    assert text.endswith("anlatıyorum 287") and "çıkış bekliyorum" in text
    assert " … " in text and len(text) <= 3500
    # another instrument's window does not get the altın recap
    assert "çıkış bekliyorum" not in outlook.transcript_excerpt(
        segments, "02:30", "03:30", "gumus"
    )
