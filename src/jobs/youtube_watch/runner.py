"""CLI entrypoint for YouTube watch cronjob."""

from src.jobs.youtube_watch import service


def main() -> int:
    service.run_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

