"""Backfill Video.person_id for existing rows using title-based detection.

Run once after deploying the person detector:
    uv run python scripts/backfill_video_person.py [--dry-run]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, select

from src.channels.models import YouTubeChannel
from src.database import create_db_and_tables, engine, run_lightweight_migrations
from src.persons import detector as person_detector
from src.persons import service as persons_service
from src.videos.models import Video


def _bootstrap() -> None:
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.twitter_post.models  # noqa: F401
    import src.jobs.youtube_watch.models  # noqa: F401
    import src.persons.models  # noqa: F401
    import src.topics.models  # noqa: F401
    import src.videos.models  # noqa: F401

    create_db_and_tables()
    run_lightweight_migrations()


def run(dry_run: bool = False) -> None:
    _bootstrap()

    updated = skipped = errors = 0

    with Session(engine) as session:
        videos = session.exec(select(Video)).all()
        channels: dict[int, YouTubeChannel] = {
            ch.id: ch
            for ch in session.exec(select(YouTubeChannel)).all()
            if ch.id is not None
        }

        for video in videos:
            try:
                channel = channels.get(video.channel_id) if video.channel_id else None
                channel_handle = channel.channel_handle if channel else None
                slug = person_detector.detect_person(video.title, channel_handle)
                if not slug:
                    skipped += 1
                    continue

                if not dry_run:
                    person = persons_service.upsert_known_person(session, slug)
                    if person and video.person_id != person.id:
                        video.person_id = person.id
                        session.add(video)
                        updated += 1
                    else:
                        skipped += 1
                else:
                    print(f"  [dry] video_id={video.id} title={video.title!r} → {slug}")
                    updated += 1

            except Exception as exc:
                print(f"  ERROR video_id={video.id}: {exc}")
                errors += 1

        if not dry_run:
            session.commit()

    print(f"\nDone — updated={updated} skipped={skipped} errors={errors}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Print detections without writing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
