"""Backfill instrument outlook for already-ingested videos.

Usage (from the repo root, with the target DATABASE_URL + MINIMAX_* in env):
  uv run python scripts/backfill_outlook.py --dry-run --limit 20      # print, write nothing
  uv run python scripts/backfill_outlook.py --limit 200               # classify + save
  uv run python scripts/backfill_outlook.py --all-versions            # re-run older versions too

Only videos that have at least one tracked-instrument mention without the current
outlook_version are processed, newest first. One LLM call per video.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlmodel import Session, select

import src.channels.models  # noqa: E402,F401 — register all tables
import src.classification.models  # noqa: E402,F401
import src.jobs.twitter_post.models  # noqa: E402,F401
import src.jobs.youtube_watch.models  # noqa: E402,F401
import src.persons.models  # noqa: E402,F401
import src.topics.models  # noqa: E402,F401
import src.videos.models  # noqa: E402,F401
from src.classification import outlook
from src.classification.models import TopicMention
from src.database import engine
from src.llm.prompts import OUTLOOK_PROMPT_VERSION
from src.topics.models import Topic
from src.videos.models import Video


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="max videos to process")
    ap.add_argument("--dry-run", action="store_true", help="classify and print, do not save")
    ap.add_argument("--all-versions", action="store_true", help="also redo other versions")
    ap.add_argument("--video-id", type=int, action="append", help="only these video ids")
    args = ap.parse_args()

    with Session(engine) as session:
        stmt = (
            select(Video)
            .join(TopicMention, TopicMention.video_id == Video.id)
            .join(Topic, TopicMention.topic_id == Topic.id)
            .where(Topic.slug.in_(list(outlook.INSTRUMENTS)))
        )
        if args.video_id:
            stmt = stmt.where(Video.id.in_(args.video_id))
        elif not args.all_versions and not args.dry_run:
            stmt = stmt.where(
                (TopicMention.outlook_version.is_(None))
                | (TopicMention.outlook_version != OUTLOOK_PROMPT_VERSION)
            )
        stmt = stmt.distinct().order_by(Video.published_at.desc().nulls_last(), Video.id.desc())
        videos = session.exec(stmt.limit(args.limit)).all()

        done = failed = 0
        for video in videos:
            try:
                res = outlook.classify_video(session, video, dry_run=args.dry_run)
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                failed += 1
                print(json.dumps({"video_id": video.id, "error": str(exc)[:200]}), flush=True)
                continue
            done += 1
            for mid, fields in res.items():
                print(json.dumps({"video_id": video.id, "mention_id": mid, **fields},
                                 ensure_ascii=False), flush=True)
        print(f"# videos={len(videos)} done={done} failed={failed} dry_run={args.dry_run}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
