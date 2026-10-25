import json
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, SQLModel, select

import src.market_inference.models  # noqa: F401  (registers the inference tables)
from src.channels.models import YouTubeChannel
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.market_inference import router, service
from src.market_inference.models import (
    MarketInferenceRun,
    MarketInferenceSource,
    MarketInferenceTopic,
)
from src.persons.models import Person, VideoParticipants
from src.topics.models import Topic
from src.videos.models import Video

NOW = datetime.now(UTC).replace(tzinfo=None)


def _setup(session: Session) -> dict:
    SQLModel.metadata.create_all(session.get_bind())
    host = YouTubeChannel(name="Devrim Akyıl", slug="devrim-akyil", channel_handle="devrimakyl")
    show = YouTubeChannel(
        name="Mesele Ekonomi", slug="mesele-ekonomi", channel_handle="meseleekonomi"
    )
    owner = Person(name="Kanal Sahibi", slug="kanal-sahibi")
    guest = Person(name="Atilla Yeşilada", slug="atilla-yesilada")
    session.add_all([host, show, owner, guest])
    session.commit()
    topic = session.exec(select(Topic).where(Topic.slug == "enflasyon")).one()
    return {"host": host, "show": show, "owner": owner, "guest": guest, "topic": topic}


def _mention(
    session, ctx, channel, n, *, speaker=None, video_person=None, published_at=None,
    confidence=0.9, video=None,
):
    if video is None:
        video = Video(
            channel_id=channel.id,
            person_id=video_person.id if video_person else None,
            video_url=f"https://youtube.com/watch?v=v{n}",
            video_id=f"v{n}",
            title=f"Video {n}",
            published_at=published_at,
        )
        session.add(video)
        session.commit()
    mention = TopicMention(
        video_id=video.id,
        channel_id=channel.id,
        person_id=speaker.id if speaker else None,
        topic_id=ctx["topic"].id,
        summary=f"Özet {n}",
        confidence=confidence,
        outlook="up",
        outlook_explicit=True,
        outlook_quote="yüzde 30'da kalır",
    )
    session.add(mention)
    session.commit()
    return video


def _detected(session, video, *persons):
    names = json.dumps([{"person_id": p.id, "name": p.name, "role": "guest"} for p in persons])
    session.add(VideoParticipants(video_id=video.id, participants_json=names, version="t"))
    session.commit()


def _window():
    return NOW - timedelta(days=1), NOW + timedelta(days=1)


def test_agenda_view_on_a_one_guest_episode_is_credited_to_the_guest(session: Session):
    ctx = _setup(session)
    other = Person(name="Yunus Emre Erdölen", slug="yunus-emre-erdolen")
    session.add(other)
    session.commit()
    # Agenda mentions never get a speaker (the outlook step covers instruments only).
    solo = _mention(session, ctx, ctx["show"], 1, video_person=ctx["owner"])
    _detected(session, solo, ctx["guest"])
    panel = _mention(session, ctx, ctx["show"], 2, video_person=ctx["owner"])
    _detected(session, panel, ctx["guest"], other)
    host_video = _mention(session, ctx, ctx["host"], 3)

    sources = {s.video_id: s for s in service._collect_sources(session, "enflasyon", *_window())}

    assert sources[solo.id].person_name == "Atilla Yeşilada"
    assert sources[solo.id].weight == 2.0  # inference_weights.yaml: Atilla on Mesele
    # Panel without a speaker: nobody to credit, and the weight config drops unnamed
    # Mesele views. Never the video owner (the headline guest).
    assert panel.id not in sources
    # Single-host channel without a person on the mention stays a source.
    assert sources[host_video.id].person_name is None
    assert "Konuşan: - / Kanal: Devrim Akyıl" in service._build_sources_block(
        [sources[host_video.id]]
    )


def test_guest_is_named_only_once_participants_are_detected(session: Session):
    ctx = _setup(session)
    detected = _mention(session, ctx, ctx["show"], 1, speaker=ctx["guest"])
    _detected(session, detected, ctx["guest"])
    stale = _mention(session, ctx, ctx["show"], 2, speaker=ctx["guest"])

    sources = {s.video_id: s for s in service._collect_sources(session, "enflasyon", *_window())}
    speakers = service.speakers_by_video(session, "enflasyon", [detected.id, stale.id])

    assert sources[detected.id].person_name == "Atilla Yeşilada"
    assert sources[detected.id].outlook == "up"
    assert sources[detected.id].outlook_quote == "yüzde 30'da kalır"
    # No publish date (OI-013): dated by when the video was added.
    assert sources[detected.id].published_at == detected.created_at
    assert "Açık beklenti: yükseliş" in service._build_sources_block(list(sources.values()))
    # Speaker left from before detection: not named, so the Mesele weight rule drops it.
    assert stale.id not in sources
    assert speakers == {detected.id: ctx["guest"], stale.id: None}


def test_source_and_api_pick_the_same_mention(session: Session):
    ctx = _setup(session)
    other = Person(name="Yunus Emre Erdölen", slug="yunus-emre-erdolen")
    session.add(other)
    session.commit()
    video = _mention(session, ctx, ctx["show"], 1, speaker=other, confidence=0.6)
    _mention(session, ctx, ctx["show"], 1, speaker=ctx["guest"], confidence=0.9, video=video)
    _detected(session, video, ctx["guest"], other)

    sources = service._collect_sources(session, "enflasyon", *_window())
    speakers = service.speakers_by_video(session, "enflasyon", [video.id])

    assert [s.person_name for s in sources] == ["Atilla Yeşilada"]
    assert speakers[video.id] == ctx["guest"]


def test_carried_over_topic_keeps_its_sources(session: Session):
    ctx = _setup(session)
    video = _mention(session, ctx, ctx["host"], 3, speaker=ctx["guest"])
    prev_run = MarketInferenceRun(run_date="2026-10-24", status="success")
    run = MarketInferenceRun(run_date="2026-10-25", status="running")
    session.add_all([prev_run, run])
    session.commit()
    prior = MarketInferenceTopic(
        run_id=prev_run.id, topic_key="enflasyon", direction="up", summary="Önceki özet."
    )
    session.add(prior)
    session.add(
        MarketInferenceSource(
            run_id=prev_run.id, topic_key="enflasyon", video_id=video.id, contribution_note="Not."
        )
    )
    session.commit()

    service._carry_over_sources(session, prior, run.id, "enflasyon")
    session.commit()

    copied = session.exec(
        select(MarketInferenceSource).where(MarketInferenceSource.run_id == run.id)
    ).all()
    assert [(c.video_id, c.contribution_note) for c in copied] == [(video.id, "Not.")]


def test_api_source_shows_speaker_and_date(session: Session):
    ctx = _setup(session)
    published = datetime(2026, 10, 20, 9, 0)
    video = _mention(
        session, ctx, ctx["show"], 4,
        speaker=ctx["guest"], video_person=ctx["owner"], published_at=published,
    )
    _detected(session, video, ctx["guest"])
    run = MarketInferenceRun(run_date="2026-10-25", status="success")
    session.add(run)
    session.commit()
    topic = MarketInferenceTopic(
        run_id=run.id, topic_key="enflasyon", direction="up", summary="Özet."
    )
    session.add(topic)
    session.add(MarketInferenceSource(run_id=run.id, topic_key="enflasyon", video_id=video.id))
    session.commit()

    out = router._build_topic_out(session, topic, run.id)

    assert out.sources[0].person_name == "Atilla Yeşilada"
    assert out.sources[0].published_at == published


def test_agenda_prompt_forbids_direction_in_summary(monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr(llm_service, "_is_minimax_configured", lambda: True)

    def fake_call(prompt):
        prompts.append(prompt)
        return {"direction": "mixed", "confidence": 0.6, "summary": "Özet.", "contributions": []}

    monkeypatch.setattr(llm_service, "_call_llm_json", fake_call)

    for agenda in (True, False):
        llm_service.generate_market_inference_json(
            topic_key="enflasyon", topic_label="Enflasyon", sources_block="-", agenda=agenda
        )

    assert "GÜNDEM özetidir" in prompts[0]
    assert "GÜNDEM özetidir" not in prompts[1]
    assert all("ÖZET YAZIM KURALLARI" in p and "{{" not in p for p in prompts)
