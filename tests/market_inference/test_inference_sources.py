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
from src.persons.models import Person
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


def _mention(session, ctx, channel, n, *, speaker=None, video_person=None, published_at=None):
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
        confidence=0.9,
        outlook="up",
        outlook_explicit=True,
        outlook_quote="yüzde 30'da kalır",
    )
    session.add(mention)
    session.commit()
    return video


def test_sources_credit_the_speaker_and_skip_unnamed_guest_shows(session: Session):
    ctx = _setup(session)
    # Guest on a multi-guest show: credited to the guest, not the video owner.
    guest_video = _mention(
        session, ctx, ctx["show"], 1, speaker=ctx["guest"], video_person=ctx["owner"]
    )
    # Multi-guest show with no named speaker: nobody to credit.
    _mention(session, ctx, ctx["show"], 2)

    sources = service._collect_sources(
        session, "enflasyon", NOW - timedelta(days=1), NOW + timedelta(days=1)
    )

    assert [s.video_id for s in sources] == [guest_video.id]
    assert sources[0].person_name == "Atilla Yeşilada"
    assert sources[0].outlook == "up"
    assert sources[0].outlook_quote == "yüzde 30'da kalır"
    # No publish date (OI-013): dated by when the video was added.
    assert sources[0].published_at == guest_video.created_at
    assert "Konuşan: Atilla Yeşilada" in service._build_sources_block(sources)


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
