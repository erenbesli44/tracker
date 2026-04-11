# Development Plan - YouTube Watch Cronjob

Status: Draft (development scope only)  
Date: 2026-04-11  
Owner: Backend/API

## 1. Objective

Implement a minimal scheduled YouTube watcher that:

1. polls tracked channels,
2. detects newly published videos,
3. ingests each new video safely (idempotent),
4. fetches transcript,
5. triggers existing pipeline steps.

This plan focuses only on application development. Deployment/orchestration setup is intentionally out of scope.

## 2. Current Baseline (from code)

Available building blocks:

1. One-shot ingestion endpoint and service (`POST /ingestions/youtube`).
2. Transcript fetch path for existing videos (`POST /videos/{video_id}/transcript/fetch`).
3. Video-level deduplication via unique `video.video_url`.
4. Tracked channel identity can use `person.platform_handle` (`UC...` channel id).

Gap:

- No job module currently exists to poll channels and orchestrate end-to-end ingestion automatically.

## 3. Target Module Layout (separate folder)

New job area:

```text
src/jobs/
  __init__.py
  README.md
  youtube_watch/
    __init__.py
    README.md
    runner.py
    service.py
    provider.py
    repository.py
    schemas.py
    exceptions.py
```

Responsibilities:

1. `runner.py`: entrypoint (`python -m src.jobs.youtube_watch.runner`), one-run lifecycle.
2. `service.py`: channel loop, new-video decisions, orchestration.
3. `provider.py`: YouTube data source integration.
4. `repository.py`: DB access for tracked channels, watch state, run logs.
5. `schemas.py`: typed DTOs for provider items and run summary.
6. `exceptions.py`: typed error categories for retry policy.

## 4. Data Source Strategy

Default strategy: YouTube channel feed (`videos.xml`) per channel id.

Why:

1. low complexity,
2. low/no quota pressure compared to expensive search-based approaches,
3. sufficient for periodic new-video detection.

Planned provider interface:

1. `list_recent_videos(channel_id: str, limit: int) -> list[ProviderVideoItem]`
2. `ProviderVideoItem` fields:
   - `video_id`
   - `video_url`
   - `title`
   - `published_at`
   - `channel_id`

## 5. Persistence Plan

Add minimal watcher state tables (SQLModel):

1. `youtube_channel_watch_state`
   - `id` (PK)
   - `person_id` (FK `person.id`, unique/indexed)
   - `channel_id` (indexed)
   - `last_seen_video_published_at` (nullable datetime)
   - `last_seen_video_id` (nullable)
   - `last_polled_at` (nullable datetime)
   - `last_success_at` (nullable datetime)
   - `last_error` (nullable text)
   - `updated_at` (datetime)
2. `youtube_watch_job_run`
   - `id` (PK)
   - `started_at`, `finished_at`
   - `status` (`success` / `partial_fail` / `failed`)
   - `channels_scanned`
   - `videos_detected`
   - `videos_ingested`
   - `videos_skipped`
   - `errors_count`
   - `error_summary` (nullable text)

Notes:

1. Keep `video.video_url` as canonical dedupe key.
2. Do not store secrets in DB.

## 6. Orchestration Flow

For each run:

1. Create `youtube_watch_job_run` record with `started_at`.
2. Query tracked channels:
   - `person.platform = 'youtube'`
   - `person.platform_handle IS NOT NULL`
3. For each channel:
   - fetch recent videos from provider,
   - compare against watch state + existing `video.video_url`,
   - keep only unseen candidates.
4. For each unseen video:
   - create/reuse video via `videos_service.create/get_by_url`,
   - fetch transcript via `videos_service.fetch_transcript_from_youtube` + add/update transcript,
   - trigger existing classification/summary pipeline step (manual/placeholder until full LLM phase),
   - persist success/failure result.
5. Update watch state (`last_polled_at`, last seen markers, last error).
6. Finalize run record (`finished_at`, counters, status).

Idempotency rules:

1. If video already exists by URL, mark as skipped.
2. Re-running the same window must not duplicate video/transcript records.
3. State updates occur after per-video processing outcome is known.

## 7. Retry Policy

Retry only transient operations:

1. provider fetch errors (network/timeout/5xx),
2. temporary transcript provider failures.

Do not retry:

1. validation errors (invalid channel id/url),
2. permanent transcript-unavailable errors.

Policy:

1. max retries: 2
2. backoff: 1s then 3s
3. record final error in watch state + run log.

## 8. Development Phases

### Phase A - Scaffold + Contracts

1. add `src/jobs/youtube_watch/*` skeleton files.
2. define DTOs and service interfaces.
3. define error taxonomy and retry helper contract.

### Phase B - Persistence Layer

1. implement SQLModel tables for watch state and job run logs.
2. register models during startup import flow.
3. implement repository CRUD helpers.

### Phase C - Provider Layer

1. implement channel feed client in `provider.py`.
2. parse feed entries into typed DTOs.
3. add robust parsing/error mapping tests.

### Phase D - Job Service Orchestration

1. implement channel iteration and candidate filtering.
2. implement per-video ingest + transcript sequence.
3. implement retry wrapper and counters.
4. finalize run summary object + persisted run row.

### Phase E - Runner + Test Suite

1. implement `runner.py` command entrypoint.
2. add unit tests and focused integration tests.
3. add development docs for local execution and troubleshooting.

## 9. Test Plan

Required scenarios:

1. New video detected:
   - channel returns unseen video,
   - video created + transcript stored,
   - counters increment as expected.
2. Duplicate video skipped:
   - existing `video.video_url`,
   - no duplicate rows,
   - skip counter increments.
3. Transcript unavailable:
   - provider returns transcript-unavailable error,
   - video may remain created,
   - error logged without crashing full run.
4. Partial failure + retry:
   - first call fails transiently, second succeeds,
   - retry count applied,
   - final status success/partial_fail based on aggregate results.

Additional recommended scenarios:

1. invalid channel id in `platform_handle`,
2. feed parse error,
3. empty channel feed,
4. multi-channel mixed outcomes in one run.

## 10. Acceptance Criteria

1. Single command runs one full polling cycle across all tracked channels.
2. New videos are ingested once; reruns stay idempotent.
3. Transient failures are retried with backoff.
4. Run-level and channel-level outcomes are persisted for diagnostics.
5. Tests cover the four required core scenarios.

## 11. Immediate Next Build Tasks

1. Implement Phase A files with concrete interfaces.
2. Implement persistence models and repository methods.
3. Implement feed provider + parser.
4. Implement orchestration service with retry helper.
5. Add tests and validate with `pytest`.

