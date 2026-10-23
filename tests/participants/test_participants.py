"""OI-018: views on multi-guest channels belong to the guest who said them."""

from httpx import AsyncClient
from sqlmodel import Session, select

from src.channels.models import YouTubeChannel
from src.classification import outlook
from src.classification.models import TopicMention
from src.llm import service as llm_service
from src.persons import detector
from src.persons import participants as p
from src.persons.models import Person, VideoParticipants
from src.videos.models import Video


def test_title_names_reads_people_after_last_pipe():
    title = "Uzun Vadede Altın? | Piyasanın Efendisi | Atilla Yeşilada & Perihan Tantuğ"
    assert p.title_names(title) == ["Atilla Yeşilada", "Perihan Tantuğ"]
    assert p.title_names("Seans Başlarken 22.10.2026") == []
    assert p.title_names("Konu | Piyasanın Efendisi") == ["Piyasanın Efendisi"]  # LLM decides
    assert p.title_names("Konu | 2027 Hedefleri") == []


def test_normalize_name_is_turkish_and_accent_insensitive():
    assert p.normalize_name("ATİLLA YEŞİLADA") == p.normalize_name("Atilla Yesilada")


def test_resolve_participants_keeps_title_spelling_and_drops_host_and_mentions():
    payload = {"participants": [
        {"name": "Atila Yeşilada", "role": "guest", "finance": True},  # model misspelling
        {"name": "Perihan Tantuğ", "role": "host", "finance": False},
        {"name": "Mehmet Şimşek", "role": "guest", "finance": False},  # not a finance guest
        {"name": "Erdal Sağlam", "role": "guest", "finance": True},
    ]}
    out = p.resolve_participants(payload, ["Atilla Yeşilada", "Perihan Tantuğ"])
    by_name = {x["name"]: x["role"] for x in out}
    assert len(out) == 3  # the misspelling did not create a second Atilla
    assert by_name["Atilla Yeşilada"] == "guest"
    assert by_name["Perihan Tantuğ"] == "host"
    assert by_name["Erdal Sağlam"] == "guest"
    assert "Mehmet Şimşek" not in by_name


def test_speaker_person_id_matches_full_or_partial_names_only_among_guests():
    parts = [
        {"person_id": 1, "name": "Atilla Yeşilada", "role": "guest"},
        {"person_id": 2, "name": "Yunus Emre Erdölen", "role": "guest"},
        {"person_id": None, "name": "Perihan Tantuğ", "role": "host"},
    ]
    assert p.speaker_person_id("Atilla Yeşilada", parts) == 1
    assert p.speaker_person_id("Atilla Bey", parts) == 1
    assert p.speaker_person_id("Erdölen", parts) == 2
    assert p.speaker_person_id("sunucu", parts) is None
    assert p.speaker_person_id("Perihan Tantuğ", parts) is None
    assert p.speaker_person_id("bilinmiyor", parts) is None


def test_multi_guest_channels_come_from_config():
    assert detector.is_multi_guest_channel("@MeseleEkonomi")
    assert detector.is_multi_guest_channel("@ncminvest-tr")
    assert not detector.is_multi_guest_channel("@ATİLLAYEŞİLADA")
    # a multi-guest channel never falls back to a default person
    assert detector.detect_person("Seans Başlarken", "@MeseleEkonomi") is None
    assert detector.detect_person("Yeni video", "@yatirim101") == "yatirim-101"
    assert detector.detect_channel_default("@ATİLLAYEŞİLADA") == "atilla-yesilada"


async def _multi_guest_video(client: AsyncClient, session: Session) -> tuple[int, dict[str, int]]:
    topics = {t["slug"]: t["id"] for t in (await client.get("/topics/")).json()}
    channel = await client.post(
        "/channels/", json={"name": "Mesele Ekonomi", "channel_handle": "@MeseleEkonomi"}
    )
    video = await client.post(
        "/videos/",
        json={
            "channel_id": channel.json()["id"],
            "video_url": "https://www.youtube.com/watch?v=mesele00001",
            "title": "Piyasalar Gergin! | Atilla Yeşilada & Yunus Emre Erdölen",
        },
    )
    video_id = video.json()["id"]
    await client.post(f"/videos/{video_id}/transcript", json={"raw_text": "Transkript."})
    # the video's headline guest (first title alias) is Atilla
    session.add(Person(name="Atilla Yeşilada", slug="atilla-yesilada"))
    session.commit()
    v = session.get(Video, video_id)
    v.person_id = session.exec(select(Person).where(Person.slug == "atilla-yesilada")).one().id
    session.add(v)
    session.commit()
    mentions = [
        {"topic_id": topics["doviz-kur"], "summary": "Döviz: kur yükselir.", "confidence": 0.9},
        {"topic_id": topics["bist-turk-piyasalari"], "summary": "Borsa: düşüş.", "confidence": 0.9},
        {"topic_id": topics["altin"], "summary": "Altın: yatay.", "confidence": 0.9},
        {"topic_id": topics["ic-siyaset"], "summary": "Siyaset: seçim.", "confidence": 0.8},
    ]
    res = await client.post(f"/videos/{video_id}/classify", json={"topic_mentions": mentions})
    assert res.status_code == 201
    return video_id, topics


async def test_classify_attributes_each_view_to_its_guest(
    client: AsyncClient, session: Session, monkeypatch
):
    video_id, topics = await _multi_guest_video(client, session)
    m = {mt.topic_id: mt for mt in session.exec(select(TopicMention)).all()}
    dolar, bist = m[topics["doviz-kur"]], m[topics["bist-turk-piyasalari"]]
    altin = m[topics["altin"]]
    # mentions on a multi-guest channel are created without the headline guest
    assert {mt.person_id for mt in m.values()} == {None}

    monkeypatch.setattr(llm_service, "generate_participants_json", lambda **_: {"participants": [
        {"name": "Atilla Yeşilada", "role": "guest", "finance": True},
        {"name": "Yunus Emre Erdölen", "role": "guest", "finance": True},
        {"name": "Ahmet Sunucu", "role": "host", "finance": False},
    ]})
    seen = {}

    def fake_outlook(**kwargs):
        seen.update(kwargs)
        return {"items": [
            {"mention_id": dolar.id, "outlook": "up", "explicit": True,
             "speaker": "Atilla Yeşilada"},
            {"mention_id": bist.id, "outlook": "down", "explicit": True, "speaker": "Erdölen"},
            {"mention_id": altin.id, "outlook": "flat", "explicit": True, "speaker": "sunucu"},
        ]}

    monkeypatch.setattr(llm_service, "generate_outlook_json", fake_outlook)
    outlook.classify_video(session, session.get(Video, video_id))
    assert "Konuşmacı listesi" in seen["speaker_name"]
    assert "Yunus Emre Erdölen" in seen["speaker_name"]

    session.expire_all()
    persons = {pp.slug: pp for pp in session.exec(select(Person)).all()}
    assert "yunus-emre-erdolen" in persons  # new guest became a person
    channels = session.exec(select(YouTubeChannel)).all()
    assert len(channels) == 1  # ... without a channel row of its own
    assert session.get(TopicMention, dolar.id).person_id == persons["atilla-yesilada"].id
    assert session.get(TopicMention, bist.id).person_id == persons["yunus-emre-erdolen"].id
    assert session.get(TopicMention, altin.id).person_id is None  # host's view: not attributed
    assert session.get(VideoParticipants, video_id) is not None

    body = (await client.get("/topics/doviz-kur/opinions")).json()
    group = body["channel_opinions"][0]
    assert group["multi_guest"] is True
    assert group["entries"][0]["person_name"] == "Atilla Yeşilada"

    appearances = {a["slug"]: a for a in (await client.get("/persons/appearances")).json()}
    assert appearances["yunus-emre-erdolen"]["channel_slugs"] == ["mesele-ekonomi"]
    assert appearances["yunus-emre-erdolen"]["own_channel_slug"] is None


async def test_unattributed_multi_guest_views_carry_no_person(
    client: AsyncClient, session: Session
):
    video_id, topics = await _multi_guest_video(client, session)
    dolar = session.exec(
        select(TopicMention).where(TopicMention.topic_id == topics["doviz-kur"])
    ).one()
    # simulate data from before this change: the headline guest on the mention
    dolar.person_id = session.get(Video, video_id).person_id
    session.add(dolar)
    session.commit()

    body = (await client.get("/topics/doviz-kur/opinions")).json()
    entry = body["channel_opinions"][0]["entries"][0]
    assert entry["person_name"] is None  # not attributed yet → not shown under a name
    assert (await client.get("/persons/appearances")).json() == []

    assert outlook.clear_unattributed_multi_guest_persons(session) == 1
    session.expire_all()
    assert session.get(TopicMention, dolar.id).person_id is None
    assert outlook.pending_attribution_video_ids(session, 10) == [video_id]


def test_caption_names_only_match_known_people(session: Session):
    session.add(Person(name="Cüneyt Paksoy", slug="cuneyt-paksoy"))
    session.commit()
    # misspelt by auto-captions → the known person
    assert p.find_or_create_person(session, "Cüneyt Faksoy", create=False, fuzzy=True).slug == (
        "cuneyt-paksoy"
    )
    # a caption-only name nobody knows is not created
    assert p.find_or_create_person(session, "Murat Sın", create=False, fuzzy=True) is None
    assert session.exec(select(Person).where(Person.slug == "murat-sin")).first() is None
    # a title name may create a new person
    assert p.find_or_create_person(session, "Erdal Sağlam").slug == "erdal-saglam"
    # config alias of a known person, spelt as in the title
    assert p.find_or_create_person(session, "Taner Genek").slug == "taner-genek"
