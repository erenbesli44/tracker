"""CLI entrypoint for the research watch job (Coolify scheduled task).

Usage:
    python -m src.jobs.research_watch.runner
    python -m src.jobs.research_watch.runner --backfill --since 2026-01-01 [--max-items 20]

Exit codes: 0 success or skipped by the daily limit, 1 partial failure, 2 fatal / all failed.
"""

import argparse
import logging
import sys
from datetime import date

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.jobs.research_watch.runner", description="Research watch job."
    )
    parser.add_argument("--backfill", action="store_true", help="bypass the daily run limit")
    parser.add_argument("--since", type=date.fromisoformat, metavar="YYYY-MM-DD")
    parser.add_argument("--max-items", type=int, default=None, metavar="N")
    args = parser.parse_args(argv)
    if args.backfill != (args.since is not None):
        parser.error("--backfill and --since go together")
    if args.max_items is not None and not args.backfill:
        parser.error("--max-items applies to --backfill only")
    return args


def _bootstrap() -> None:
    """Register every table, then create missing ones (column migrations run at API start)."""
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.broker_watch.models  # noqa: F401
    import src.jobs.research_watch.models  # noqa: F401
    import src.jobs.twitter_post.models  # noqa: F401
    import src.jobs.youtube_watch.models  # noqa: F401
    import src.market_inference.models  # noqa: F401
    import src.persons.models  # noqa: F401
    import src.topics.models  # noqa: F401
    import src.videos.models  # noqa: F401
    from src.database import create_db_and_tables

    create_db_and_tables()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    from src.jobs.research_watch.config import load_research_config

    try:
        config = load_research_config()
    except (FileNotFoundError, ValueError) as exc:
        logger.error("Config error: %s", exc)
        return 2
    if not config.enabled_sources:
        logger.warning("No enabled sources in watched_research.yaml. Nothing to do.")
        return 0
    try:
        _bootstrap()
    except Exception as exc:
        logger.error("Database bootstrap failed: %s", exc, exc_info=True)
        return 2

    from src.database import engine
    from src.jobs.research_watch import service

    summary = service.run_once(engine, config, since=args.since, max_items=args.max_items)
    logger.info(
        "Done: status=%s seen=%d new=%d digests_ok=%d flagged=%d errors=%d",
        summary.status,
        summary.items_seen,
        summary.items_new,
        summary.digests_ok,
        summary.digests_flagged,
        len(summary.errors),
    )
    return {"success": 0, service.SKIPPED_RATE_LIMIT: 0, "partial_fail": 1}.get(summary.status, 2)


if __name__ == "__main__":
    sys.exit(main())
