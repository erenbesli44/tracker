"""CLI entrypoint for the Twitter posting cronjob.

Usage:
    python -m src.jobs.twitter_post.runner

Exit codes:
    0 — all candidates posted (or none found)
    1 — partial success (some posted, some failed)
    2 — fatal failure (DB unavailable or everything failed)
"""

import logging
import sys

logger = logging.getLogger(__name__)


def _bootstrap() -> None:
    """Import all SQLModel tables so metadata is registered, then sync schema."""
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.twitter_post.models  # noqa: F401
    import src.jobs.youtube_watch.models  # noqa: F401
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
    from src.jobs.twitter_post import service

    summary = service.run_once(engine)

    logger.info(
        "Done — found=%d posted=%d skipped=%d failed=%d",
        summary.candidates_found,
        summary.posted,
        summary.skipped,
        summary.failed,
    )

    if summary.failed == 0:
        return 0
    if summary.posted > 0:
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
