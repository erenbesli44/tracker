"""CLI entrypoint for the broker watch cronjob.

Usage:
    python -m src.jobs.broker_watch.runner
    python -m src.jobs.broker_watch.runner --backfill --since 2026-01-01 [--delay 1.0]
        [--max-documents 40]   # one-off backfill, see service.run_once

Exit codes:
    0 — all sources processed without errors
    1 — partial success (some sources/documents failed)
    2 — fatal failure (config error, DB unavailable, or all sources failed)
"""

import argparse
import logging
import sys
from datetime import date

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_DELAY = 1.0
DEFAULT_BACKFILL_MAX_DOCUMENTS = 40


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.jobs.broker_watch.runner", description="Broker watch job."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="one-off backfill: bypass max_runs_per_day and follow the sources back to --since",
    )
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="oldest document date to collect (required with --backfill)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_BACKFILL_DELAY,
        metavar="SECONDS",
        help="courtesy delay between live requests in a backfill "
        f"(default {DEFAULT_BACKFILL_DELAY})",
    )
    parser.add_argument(
        "--max-documents",
        type=int,
        default=DEFAULT_BACKFILL_MAX_DOCUMENTS,
        metavar="N",
        help="backfill: a source whose discovery would push the run past N documents is not "
        f"fetched (default {DEFAULT_BACKFILL_MAX_DOCUMENTS})",
    )
    args = parser.parse_args(argv)
    if args.backfill != (args.since is not None):
        parser.error("--backfill and --since go together")
    return args


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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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

    if args.backfill:
        summary = service.run_once(
            engine,
            config,
            since=args.since,
            request_delay=args.delay,
            max_documents=args.max_documents,
        )
    else:
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
