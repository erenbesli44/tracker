"""
Dev seed script — loads Taner Genek data directly via SQLModel (no HTTP).

Usage:
    uv run python scripts/load_dev_data.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make src importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, select

from src.database import create_db_and_tables, engine
from src.persons.models import Person
from src.topics.service import seed_topics
from src.topics.constants import TOPIC_SEED
from src.videos.models import Transcript, Video
from src.classification.models import TopicMention
from src.topics.models import Topic

# --- Transcript files ---
DUMMY_DIR = Path(__file__).parent.parent / "dummy" / "taner_genek"
TRANSCRIPT_1 = (DUMMY_DIR / "example-1").read_text(encoding="utf-8")
TRANSCRIPT_2 = (DUMMY_DIR / "example-2").read_text(encoding="utf-8")


def _utc(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str).replace(tzinfo=None)


def run() -> None:
    create_db_and_tables()

    with Session(engine) as session:
        seed_topics(session, TOPIC_SEED)

        # --- Person ---
        existing = session.exec(select(Person).where(Person.slug == "taner-genek")).first()
        if existing:
            person = existing
            print("Person already exists, skipping.")
        else:
            person = Person(
                name="Taner Genek",
                slug="taner-genek",
                platform="youtube",
                platform_handle="@tanergenek",
                bio="NCM Investment trader ve piyasa yorumcusu.",
            )
            session.add(person)
            session.commit()
            session.refresh(person)
            print(f"Created person: {person.name} (id={person.id})")

        # --- Videos ---
        def get_or_create_video(url: str, **kwargs) -> Video:
            v = session.exec(select(Video).where(Video.video_url == url)).first()
            if v:
                return v
            v = Video(person_id=person.id, video_url=url, **kwargs)
            import re
            match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
            v.video_id = match.group(1) if match else None
            session.add(v)
            session.commit()
            session.refresh(v)
            print(f"Created video: {v.title or url} (id={v.id})")
            return v

        video1 = get_or_create_video(
            "https://www.youtube.com/watch?v=bnk8WqphWMw",
            title="Trader'ın Not Defteri #45 - Piyasa Değerlendirmesi",
            published_at=_utc("2026-03-28T20:00:00"),
        )
        video2 = get_or_create_video(
            "https://www.youtube.com/watch?v=TANERGENEK002",
            title="Trader'ın Not Defteri #46 - Altın ve Piyasa Yorumu",
            published_at=_utc("2026-04-03T20:00:00"),
        )

        # --- Transcripts ---
        def add_transcript_if_missing(video: Video, raw_text: str) -> None:
            existing_t = session.exec(
                select(Transcript).where(Transcript.video_id == video.id)
            ).first()
            if existing_t:
                return
            t = Transcript(video_id=video.id, raw_text=raw_text, language="tr")
            session.add(t)
            session.commit()
            print(f"Added transcript for video id={video.id}")

        add_transcript_if_missing(video1, TRANSCRIPT_1)
        add_transcript_if_missing(video2, TRANSCRIPT_2)

        # --- Helper: look up topic by slug ---
        def get_topic(slug: str) -> Topic:
            t = session.exec(select(Topic).where(Topic.slug == slug)).first()
            if not t:
                raise RuntimeError(f"Topic '{slug}' not found — run seed_topics first.")
            return t

        # --- Classifications ---
        def clear_and_add_mentions(video: Video, mentions_data: list[dict]) -> None:
            existing = session.exec(
                select(TopicMention).where(TopicMention.video_id == video.id)
            ).all()
            for m in existing:
                session.delete(m)
            session.commit()

            for item in mentions_data:
                topic = get_topic(item["topic_slug"])
                mention = TopicMention(
                    video_id=video.id,
                    person_id=person.id,
                    topic_id=topic.id,
                    summary=item.get("summary"),
                    sentiment=item.get("sentiment"),
                    key_levels=json.dumps(item.get("key_levels", [])),
                    start_time=item.get("start_time"),
                    end_time=item.get("end_time"),
                    confidence=item.get("confidence", 0.8),
                )
                session.add(mention)
            session.commit()
            print(f"Added {len(mentions_data)} mentions for video id={video.id}")

        mentions_video1 = [
            {
                "topic_slug": "borsa-istanbul",
                "summary": "BIST100'de 13.100 desteği kritik. Kırılırsa 12.900 ardından 12.300 görülebilir. Yukarıda 13.400 ve 13.700 direnç.",
                "sentiment": "bearish",
                "key_levels": ["13100", "12900", "13400", "13700", "12300", "12150"],
                "start_time": "00:05:00",
                "end_time": "00:20:00",
                "confidence": 0.9,
            },
            {
                "topic_slug": "altin",
                "summary": "Altında yeni zirve sonrası kar satışı baskısı. Destek 2.850-2.900 bölgesi.",
                "sentiment": "bearish",
                "key_levels": ["2900", "2850", "3000"],
                "start_time": "00:20:00",
                "end_time": "00:28:00",
                "confidence": 0.85,
            },
            {
                "topic_slug": "gumus",
                "summary": "Gümüşte zayıflık sürüyor. 31 dolar altına sarkma riski.",
                "sentiment": "bearish",
                "key_levels": ["31", "29"],
                "start_time": "00:28:00",
                "end_time": "00:33:00",
                "confidence": 0.8,
            },
            {
                "topic_slug": "dow-jones",
                "summary": "Dow Jones'ta yön belirsizliği. Kritik teknik seviyelerde bekleme modu.",
                "sentiment": "neutral",
                "key_levels": ["42000", "40000"],
                "start_time": "00:33:00",
                "end_time": "00:40:00",
                "confidence": 0.75,
            },
            {
                "topic_slug": "bitcoin-kripto",
                "summary": "Bitcoin'de yatay hareket. 85K-90K bandında sıkışma.",
                "sentiment": "neutral",
                "key_levels": ["90000", "85000"],
                "start_time": "00:40:00",
                "end_time": "00:45:00",
                "confidence": 0.75,
            },
            {
                "topic_slug": "petrol",
                "summary": "Petrolde arz baskısı sürüyor. 70 dolar desteği önemli.",
                "sentiment": "bearish",
                "key_levels": ["70", "67"],
                "start_time": "00:45:00",
                "end_time": "00:50:00",
                "confidence": 0.8,
            },
            {
                "topic_slug": "jeopolitik",
                "summary": "Trump tarifeleri piyasalarda belirsizlik yaratıyor.",
                "sentiment": "bearish",
                "key_levels": [],
                "start_time": "00:50:00",
                "end_time": "00:55:00",
                "confidence": 0.85,
            },
        ]

        mentions_video2 = [
            {
                "topic_slug": "altin",
                "summary": "Altın 5.200 doları gördü, şimdi olası düzeltme hareketi. 4.800, 4.300, 4.100, 4.050 destek seviyeleri.",
                "sentiment": "bearish",
                "key_levels": ["5200", "4800", "4300", "4100", "4050", "3800", "3500"],
                "start_time": "00:03:00",
                "end_time": "00:18:00",
                "confidence": 0.9,
            },
            {
                "topic_slug": "gumus",
                "summary": "Gümüşte de olası satış baskısı. Altınla korelasyonlu hareket.",
                "sentiment": "bearish",
                "key_levels": ["33", "30"],
                "start_time": "00:18:00",
                "end_time": "00:23:00",
                "confidence": 0.8,
            },
            {
                "topic_slug": "dow-jones",
                "summary": "ABD endekslerinde karışık seyir. Sonuç belirsiz.",
                "sentiment": "neutral",
                "key_levels": ["41000", "39000"],
                "start_time": "00:23:00",
                "end_time": "00:30:00",
                "confidence": 0.75,
            },
            {
                "topic_slug": "bitcoin-kripto",
                "summary": "Bitcoin yatay. Altın yükselince kripto ilgi görüyor ama net yön yok.",
                "sentiment": "neutral",
                "key_levels": ["88000", "80000"],
                "start_time": "00:30:00",
                "end_time": "00:35:00",
                "confidence": 0.7,
            },
            {
                "topic_slug": "borsa-istanbul",
                "summary": "BIST100'de belirsizlik sürüyor. Net trend yok.",
                "sentiment": "neutral",
                "key_levels": ["13000", "12500"],
                "start_time": "00:35:00",
                "end_time": "00:42:00",
                "confidence": 0.75,
            },
            {
                "topic_slug": "petrol",
                "summary": "Petrolde düşüş eğilimi devam ediyor.",
                "sentiment": "bearish",
                "key_levels": ["68", "65"],
                "start_time": "00:42:00",
                "end_time": "00:47:00",
                "confidence": 0.8,
            },
        ]

        clear_and_add_mentions(video1, mentions_video1)
        clear_and_add_mentions(video2, mentions_video2)

        print("\nSeed completed successfully.")


if __name__ == "__main__":
    run()
