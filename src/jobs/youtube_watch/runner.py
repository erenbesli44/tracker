"""CLI entrypoint for the YouTube watch cronjob.

Usage:
    python -m src.jobs.youtube_watch.runner

Exit codes:
    0 — all channels processed without errors
    1 — partial success (some channels/videos failed)
    2 — fatal failure (config error, DB unavailable, or all channels failed)
"""

import logging
import sys

logger = logging.getLogger(__name__)


def _bootstrap() -> None:
    """Import all SQLModel tables so metadata is registered, then sync schema."""
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
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

    # Import here to avoid heavy imports before logging is configured.
    from src.jobs.youtube_watch.config import load_watch_config

    try:
        config = load_watch_config()
    except FileNotFoundError as exc:
        logger.error("Config file not found: %s", exc)
        return 2
    except ValueError as exc:
        logger.error("Config validation error: %s", exc)
        return 2

    if not config.channels:
        logger.warning("No channels configured in watched_channels.yaml. Nothing to do.")
        return 0

    logger.info("Loaded %d channel(s) from config", len(config.channels))

    try:
        _bootstrap()
    except Exception as exc:
        logger.error("Database bootstrap failed: %s", exc, exc_info=True)
        return 2

    from src.database import engine
    from src.jobs.youtube_watch import service

    summary = service.run_once(engine, config)

    logger.info(
        "Done — channels=%d detected=%d ingested=%d skipped=%d errors=%d",
        summary.channels_scanned,
        summary.videos_detected,
        summary.videos_ingested,
        summary.videos_skipped,
        summary.errors_count,
    )

    if summary.errors_count == 0:
        return 0
    if summary.videos_ingested > 0 or summary.videos_skipped > 0:
        return 1  # partial success
    return 2  # everything failed


if __name__ == "__main__":
    sys.exit(main())
