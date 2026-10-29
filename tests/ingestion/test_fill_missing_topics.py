"""Full-transcript analysis + fill-missing-topics backfill."""

from httpx import AsyncClient
from sqlmodel import Session, select

from src.classification.models import TopicMention
from src.ingestion import fill_missing_topics as fill
from src.ingestion import service
from src.llm import service as llm_service
from src.persons.models import Person
from src.videos.models import Transcript, Video


def _segments(n: int) -> list[dict]:
    return [
        {"start": i * 4.0, "duration": 4.0, "text": f"konuşma parçası {i:04d} piyasa yorumu"}
        for i in range(n)
    ]


def test_prepare_transcript_sends_a_typical_video_whole():
    # ~28 min video: on the old 12k cap a passage at ~1/4 of the video was dropped.
    segs = _segments(420)
    segs[100]["text"] = "altın 3500'lere kadar gerileyecek"
    out = service._prepare_transcript_for_llm("", transcript_segments=segs)
    assert "altın 3500'lere kadar gerileyecek" in out
    assert "[06:40-06:44]" in out
    assert "\n...\n" not in out


def test_prepare_transcript_fallback_scales_with_max_chars():
    text = " ".join(f"w{i:05d}" for i in range(20000))  # ~140k chars
    out = service._prepare_transcript_for_llm(text, max_chars=50000)
    head, middle, tail = out.split("\n...\n")
    # proportional split, no fixed 5000/2000/5000 caps
    assert len(head) > 20000 and len(middle) > 7000 and len(tail) > 20000
    assert len(out) <= 50000 + 10
    assert text.startswith(head) and text.endswith(tail)


def test_prepare_transcript_limit_comes_from_settings(monkeypatch):
    monkeypatch.setattr(service.settings, "ANALYSIS_TRANSCRIPT_MAX_CHARS", 1000)
    out = service._prepare_transcript_for_llm("x" * 5000)
    assert len(out) <= 1010 and "\n...\n" in out


async def _video(
    client: AsyncClient,
    session: Session,
    *,
    handle: str = "@t",
    person_slug: str | None = "atilla-yesilada",
) -> tuple[int, dict[str, int]]:
    topics = {t["slug"]: t["id"] for t in (await client.get("/topics/")).json()}
    channel = await client.post("/channels/", json={"name": "Kanal", "channel_handle": handle})
    video_id = (
        await client.post(
            "/videos/",
            json={
                "channel_id": channel.json()["id"],
                "video_url": "https://www.youtube.com/watch?v=fill0000001",
                "title": "Altını 4 Binde Tutan Çinliler",
            },
        )
    ).json()["id"]
    session.add(Transcript(video_id=video_id, raw_text="tam transkript", segments_json=None))
    if person_slug:
        person = Person(name="Atilla Yeşilada", slug=person_slug)
        session.add(person)
        session.commit()
        video = session.get(Video, video_id)
        video.person_id = person.id
        session.add(video)
    session.commit()
    res = await client.post(
        f"/videos/{video_id}/classify",
        json={"topic_mentions": [
            {"topic_id": topics["faiz-para-politikasi"], "summary": "Faiz: indirim.",
             "confidence": 0.9},
            {"topic_id": topics["doviz-kur"], "summary": "Döviz: kur yükselir.",
             "confidence": 0.9},
        ]},
    )
    assert res.status_code == 201
    # an existing, already classified view that must stay as it is
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    dolar.outlook, dolar.outlook_explicit, dolar.outlook_version = "up", True, "outlook-v3"
    dolar.outlook_quote = dolar.outlook_quote_clean = "kur yükselir"
    session.add(dolar)
    session.commit()
    return video_id, topics


def _analysis(**_kw) -> dict:
    seg = {"summary": "s", "start_time": "06:40", "end_time": "07:30", "confidence": 0.8}
    return {
        "key_points": ["x"],
        "summary": {"short": "YENİ ÖZET"},
        "topic_segments": [
            {**seg, "subtopic": "faiz-para-politikasi", "summary": "Faiz yeniden"},
            {**seg, "subtopic": "doviz-kur", "summary": "Dolar düşer"},
            {**seg, "subtopic": "enflasyon"},
            {**seg, "subtopic": "jeopolitik"},
            {**seg, "subtopic": "petrol-enerji"},
            {**seg, "subtopic": "bist-turk-piyasalari"},
            {**seg, "subtopic": "altin", "summary": "Altın 3500'e geriler."},
        ],
    }


def _snapshot(session: Session) -> dict[int, tuple]:
    session.expire_all()
    return {
        m.id: (m.topic_id, m.summary, m.person_id, m.outlook, m.outlook_explicit,
               m.outlook_quote_clean, m.outlook_version)
        for m in session.exec(select(TopicMention)).all()
    }


async def test_fill_adds_only_missing_topics_and_classifies_only_them(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _video(client, session)
    before = _snapshot(session)
    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)
    monkeypatch.setattr(llm_service, "generate_analysis_json", _analysis)
    seen = {}

    def fake_outlook(**kw):
        seen.update(kw)
        altin = session.exec(
            select(TopicMention).where(TopicMention.topic_id == topics["altin"])
        ).one()
        return {"items": [{"mention_id": altin.id, "outlook": "down", "explicit": True,
                           "quote": "altın 3500'e geriler"}]}

    monkeypatch.setattr(llm_service, "generate_outlook_json", fake_outlook)

    added = fill.fill_video(session, session.get(Video, video_id))
    # beyond the old 5-mention cap: altin is the 7th topic
    assert sorted(added) == sorted(
        ["enflasyon", "jeopolitik", "petrol-enerji", "bist-turk-piyasalari", "altin"]
    )

    after = _snapshot(session)
    for mid, row in before.items():
        assert after[mid] == row  # existing mentions untouched (incl. outlook fields)
    new = {r[0]: (mid, r) for mid, r in after.items() if mid not in before}
    assert len(new) == 5
    assert all(r[2] == session.get(Video, video_id).person_id for _, r in new.values())

    # outlook ran only for the new instrument mentions, not the existing dolar view
    assert "Altın" in seen["items_block"] and "Dolar" not in seen["items_block"]
    altin = new[topics["altin"]][1]
    assert (altin[3], altin[4]) == ("down", True)
    # the summary is not touched
    assert (await client.get(f"/videos/{video_id}/summary")).status_code == 404

    # a second run finds nothing new
    assert fill.fill_video(session, session.get(Video, video_id)) == []


async def test_fill_on_multi_guest_channel_keeps_person_rule(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _video(client, session, handle="@MeseleEkonomi")
    # an earlier attributed view on the existing mention must survive the subset run
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    dolar.person_id = session.get(Video, video_id).person_id
    session.add(dolar)
    session.commit()
    before = _snapshot(session)

    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)
    monkeypatch.setattr(llm_service, "generate_analysis_json", _analysis)
    monkeypatch.setattr(llm_service, "generate_participants_json", lambda **_: {"participants": [
        {"name": "Atilla Yeşilada", "role": "guest", "finance": True},
    ]})

    def fake_outlook(**kw):
        ids = [int(x.split("]")[0]) for x in kw["items_block"].split("[mention_id=")[1:]]
        return {"items": [{"mention_id": i, "outlook": "down", "explicit": True,
                           "speaker": "Atilla Yeşilada"} for i in ids]}

    monkeypatch.setattr(llm_service, "generate_outlook_json", fake_outlook)
    fill.fill_video(session, session.get(Video, video_id))

    after = _snapshot(session)
    for mid, row in before.items():
        assert after[mid] == row
    new = {r[0]: r for mid, r in after.items() if mid not in before}
    atilla = session.exec(select(Person).where(Person.slug == "atilla-yesilada")).one().id
    # instrument views go to the speaker; non-instrument mentions carry no person
    assert new[topics["altin"]][2] == atilla
    assert new[topics["enflasyon"]][2] is None
    assert new[topics["jeopolitik"]][2] is None


async def test_fill_dry_run_writes_nothing(client: AsyncClient, session: Session, monkeypatch):
    video_id, _ = await _video(client, session)
    before = _snapshot(session)
    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)
    monkeypatch.setattr(llm_service, "generate_analysis_json", _analysis)

    def no_outlook(**_):
        raise AssertionError("dry run must not classify")

    monkeypatch.setattr(llm_service, "generate_outlook_json", no_outlook)

    fill.run_fill(session.get_bind(), days=14, limit=10, workers=1, dry_run=True)
    state = fill.fill_state()
    assert state["running"] is False and state["dry_run"] is True
    assert state["scanned"] == 1 and state["failed"] == 0
    assert state["videos_with_new_topics"] == 1
    assert state["new_mentions_by_topic"]["altin"] == 1
    assert len(state["videos"][video_id]) == 5
    assert _snapshot(session) == before


async def test_fill_run_reports_failures_and_skips_old_videos(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, _ = await _video(client, session)
    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)

    def boom(**_):
        raise llm_service.LLMGenerationError("down")

    monkeypatch.setattr(llm_service, "generate_analysis_json", boom)
    fill.run_fill(session.get_bind(), days=14, limit=10, workers=1)
    state = fill.fill_state()
    assert state["failed"] == 1 and "down" in state["last_error"]

    from datetime import datetime

    video = session.get(Video, video_id)
    video.published_at = datetime(2020, 1, 1)
    session.add(video)
    session.commit()
    assert fill.candidate_video_ids(session, 14, 10) == []


async def test_fill_endpoints_single_flight_and_api_key(client: AsyncClient, monkeypatch):
    monkeypatch.setitem(fill._state, "running", True)
    res = await client.post("/ingestions/fill-missing-topics?days=7&dry_run=true")
    assert res.status_code == 202 and res.json()["status"] == "already_running"
    called = []
    monkeypatch.setattr(fill, "candidate_video_ids", lambda *a: called.append(a) or [])
    fill.run_fill(None, days=7, limit=1)  # second run while running: no-op
    assert called == []
    assert (await client.get("/ingestions/fill-missing-topics")).json()["running"] is True

    bad = await client.post("/ingestions/fill-missing-topics", headers={"X-API-Key": "wrong"})
    assert bad.status_code in (401, 403)
    assert (
        await client.get("/ingestions/fill-missing-topics", headers={"X-API-Key": "wrong"})
    ).status_code in (401, 403)


def test_classification_keeps_up_to_eight_mentions(session: Session):
    slugs = [
        "faiz-para-politikasi", "doviz-kur", "enflasyon", "jeopolitik", "petrol-enerji",
        "bist-turk-piyasalari", "altin", "gumus", "kripto-paralar",
    ]
    payload = {"topic_segments": [{"subtopic": s, "summary": "s"} for s in slugs]}
    result = service._classification_from_llm_payload(session, payload)
    # the 6th-8th topics (altin is 7th) are kept; the cap matches prompt rule 7
    assert len(result.topic_mentions) == 8
    assert len(
        service._classification_from_llm_payload(
            session, payload, max_mentions=None
        ).topic_mentions
    ) == 9
