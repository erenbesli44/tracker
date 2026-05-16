"""CLI entrypoint for the daily market inference cronjob.

Usage:
    python -m src.jobs.market_inference.runner

Exit codes:
    0 — inference completed or skipped (no new videos)
    1 — partial success (some topics failed, others succeeded)
    2 — fatal failure (DB unavailable or all topics failed)

Schedule: run daily at 19:10 local time (after YouTube watch job at ~18:00).
Crontab example (server TZ=Europe/Istanbul):
    10 19 * * * /app/.venv/bin/python -m src.jobs.market_inference.runner >> /var/log/market_inference.log 2>&1
"""

import logging
import sys

logger = logging.getLogger(__name__)


def _bootstrap() -> None:
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.market_inference  # noqa: F401
    import src.jobs.twitter_post.models  # noqa: F401
    import src.jobs.youtube_watch.models  # noqa: F401
    import src.market_inference.models  # noqa: F401
    import src.persons.models  # noqa: F401
    import src.topics.models  # noqa: F401
    import src.videos.models  # noqa: F401

    from src.database import create_db_and_tables, run_lightweight_migrations

    create_db_and_tables()
    run_lightweight_migrations()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        _bootstrap()
    except Exception as exc:
        logger.error("Database bootstrap failed: %s", exc, exc_info=True)
        return 2

    from src.database import engine
    from src.market_inference import service

    try:
        result = service.run_once(engine)
    except Exception as exc:
        logger.error("Market inference run failed fatally: %s", exc, exc_info=True)
        return 2

    status = result.get("status", "failed")
    logger.info("Market inference finished — status=%s run_id=%s", status, result.get("run_id"))

    if status in ("success", "skipped_no_new_videos"):
        return 0
    if status == "partial_fail":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
