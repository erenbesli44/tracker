"""CLI entrypoint for the broker watch cronjob.

Usage:
    python -m src.jobs.broker_watch.runner

Exit codes:
    0 — all sources processed without errors
    1 — partial success (some sources/documents failed)
    2 — fatal failure (config error, DB unavailable, or all sources failed)
"""

import logging
import sys

logger = logging.getLogger(__name__)


def _bootstrap() -> None:
    """Import all SQLModel tables so metadata is registered, then sync schema."""
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.broker_watch.models  # noqa: F401
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

    from src.jobs.broker_watch.config import load_broker_config

    try:
        config = load_broker_config()
    except FileNotFoundError as exc:
        logger.error("Config file not found: %s", exc)
        return 2
    except ValueError as exc:
        logger.error("Config validation error: %s", exc)
        return 2

    if not config.enabled_brokers:
        logger.warning("No enabled brokers in watched_brokers.yaml. Nothing to do.")
        return 0

    logger.info("Loaded %d enabled broker(s) from config", len(config.enabled_brokers))

    try:
        _bootstrap()
    except Exception as exc:
        logger.error("Database bootstrap failed: %s", exc, exc_info=True)
        return 2

    from src.database import engine
    from src.jobs.broker_watch import service

    summary = service.run_once(engine, config)

    logger.info(
        "Done — sources=%d new_docs=%d new_events=%d new_snapshots=%d errors=%d",
        summary.sources_scanned,
        summary.documents_new,
        summary.events_new,
        summary.snapshots_new,
        summary.errors_count,
    )

    if summary.errors_count == 0:
        return 0
    if summary.sources_failed < summary.sources_scanned:
        return 1  # partial success
    return 2  # every source failed


if __name__ == "__main__":
    sys.exit(main())
