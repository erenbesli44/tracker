# YouTube Watch Job

Purpose: run periodic channel polling and trigger pipeline processing for newly published videos.

Planned command entrypoint:

- `python -m src.jobs.youtube_watch.runner`

Planned internal modules:

1. `runner.py`: CLI entrypoint, run-level orchestration, process exit code.
2. `service.py`: polling + ingestion flow, idempotency checks, retries.
3. `provider.py`: YouTube channel feed/API client abstraction.
4. `schemas.py`: typed payloads for provider responses and run summaries.
5. `repository.py`: DB reads/writes for watch state and run logs.

Implementation details are defined in:

- `docs/architecture/youtube-watch-cron-development-plan.md`

