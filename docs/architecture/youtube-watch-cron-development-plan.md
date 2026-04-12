# Development Plan - YouTube Watch Cronjob (v2)

Status: Implemented
Date: 2026-04-12
Owner: Backend/API

## 1. Objective

A daily background job that:

1. Reads a **config map** (YAML file) listing which YouTube channels to track.
2. For each channel, detects newly published videos via yt-dlp.
3. For each new video, runs the full ingestion pipeline: metadata fetch, transcript, LLM summary, topic classification.
4. Logs run-level and channel-level results for diagnostics.

Runs as a standalone CLI command: `python -m src.jobs.youtube_watch.runner`

## 2. What Already Exists

### Available building blocks (no new code needed)

| Component | Location | What it does |
|-----------|----------|-------------|
| Single-video ingestion | `ingestion.service.ingest_youtube()` | Full pipeline: metadata → channel resolve → video create → transcript → LLM summary → classification |
| Channel batch ingestion | `ingestion.service.ingest_youtube_channel()` | Lists recent videos via yt-dlp, skips existing, ingests new ones |
| Video dedup | `video.video_url` unique constraint + `get_by_url()` / `get_by_video_id()` | Prevents duplicate video records |
| Transcript fetch | `videos.service.fetch_transcript_from_youtube()` | youtube-transcript-api with language fallback |
| YouTube metadata | `videos.service.fetch_youtube_metadata()` | Single yt-dlp call: title, author, channel_id, publish_date, duration |
| Channel video listing | `ingestion.service._list_recent_channel_videos()` | yt-dlp flat-playlist extraction, no video count cap |
| LLM analysis | `llm.service.generate_analysis_json()` | Gemini-powered summary + classification in one call |
| Channel model | `channels.models.YouTubeChannel` | Has `youtube_channel_id`, `channel_handle`, topic metadata |

### Key insight: minimal new code needed

`ingest_youtube_channel()` already does 90% of what the job needs per channel. The job layer is a thin wrapper: **load config → iterate channels → call existing service → log results**.

## 3. Architecture

```
                    ┌─────────────────────────┐
                    │  watched_channels.yaml   │  <-- config map
                    │  (list of channels)      │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  runner.py                │  <-- CLI entrypoint
                    │  - loads config           │
                    │  - opens DB session       │
                    │  - calls service.run_once │
                    │  - exits with status code │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │  service.py               │  <-- orchestration
                    │  - iterates channels      │
                    │  - per-channel: calls     │
                    │    ingest_youtube_channel  │
                    │  - aggregates results     │
                    │  - persists run log       │
                    └────────────┬─────────────┘
                                 │
          ┌──────────────────────┼──────────────────────┐
          │                      │                      │
  ┌───────▼───────┐    ┌────────▼────────┐    ┌────────▼────────┐
  │ ingestion     │    │ videos.service   │    │ llm.service     │
  │ .service      │    │ (transcript,     │    │ (summary +      │
  │ (full pipe)   │    │  metadata)       │    │  classification) │
  └───────────────┘    └─────────────────┘    └─────────────────┘
```

## 4. Config Map Design

### File: `config/watched_channels.yaml`

```yaml
# YouTube channels to poll daily.
# Each entry needs either youtube_channel_id (UC...) or handle (@...).
# video_count: how many recent videos to check per run (default: 5).

channels:
  - youtube_channel_id: "UCxxxxxxxxxxxxxxxxxxxxxxxx"
    name: "Paragaranti"
    video_count: 5

  - handle: "@ChannelHandle"
    name: "Another Channel"
    video_count: 3

  - handle: "@ThirdChannel"
    name: "Third Channel"
    # video_count defaults to 5

defaults:
  video_count: 5
  transcript_languages: ["tr", "en"]
```

### Schema: `src/jobs/youtube_watch/schemas.py`

```python
@dataclass
class WatchedChannel:
    """One entry from the config map."""
    name: str
    youtube_channel_id: str | None = None
    handle: str | None = None
    video_count: int = 5
    transcript_languages: list[str] | None = None

@dataclass
class WatchConfig:
    """Parsed config map."""
    channels: list[WatchedChannel]
    defaults: WatchDefaults

@dataclass
class WatchDefaults:
    video_count: int = 5
    transcript_languages: list[str] = field(default_factory=lambda: ["tr", "en"])
```

### Config loading: `src/jobs/youtube_watch/config.py`

- Reads `config/watched_channels.yaml` (path configurable via `WATCH_CONFIG_PATH` env var)
- Validates each entry has at least `youtube_channel_id` or `handle`
- Merges per-channel settings with defaults
- Raises clear error if file missing or invalid

## 5. Module Layout (updated)

```text
src/jobs/
  __init__.py
  youtube_watch/
    __init__.py
    runner.py        # CLI entrypoint
    config.py        # NEW: YAML config loader + validation
    service.py       # orchestration loop
    schemas.py       # DTOs (config + run results)
    models.py        # NEW: SQLModel tables for run logs
    repository.py    # DB reads/writes for run logs
    exceptions.py    # error taxonomy (already done)
config/
  watched_channels.yaml  # NEW: the config map
```

Changes from old plan:
- **Added** `config.py` — dedicated config loader
- **Added** `models.py` — SQLModel tables
- **Removed** `provider.py` — not needed; `_list_recent_channel_videos()` in `ingestion.service` already handles yt-dlp calls
- **Config map** replaces DB-driven channel discovery

## 6. Persistence: Run Logs

### Table: `youtube_watch_run` (new)

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | auto-increment |
| started_at | DATETIME NOT NULL | when run began |
| finished_at | DATETIME | when run ended |
| status | VARCHAR(20) NOT NULL | `success` / `partial_fail` / `failed` |
| channels_scanned | INTEGER | total channels attempted |
| videos_detected | INTEGER | total videos found across channels |
| videos_ingested | INTEGER | successfully ingested |
| videos_skipped | INTEGER | already existed |
| errors_count | INTEGER | failed ingestions |
| error_details | TEXT | JSON array of error summaries |

### Table: `youtube_watch_channel_result` (new)

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | auto-increment |
| run_id | INTEGER FK | references `youtube_watch_run.id` |
| channel_identifier | VARCHAR(255) | handle or channel_id from config |
| channel_name | VARCHAR(255) | from config |
| youtube_channel_id | VARCHAR(100) | resolved UC... id |
| videos_detected | INTEGER | |
| videos_ingested | INTEGER | |
| videos_skipped | INTEGER | |
| errors_count | INTEGER | |
| error_detail | TEXT | last error message if any |
| status | VARCHAR(20) | `success` / `partial` / `failed` / `skipped` |

No watch-state table needed. Dedup is handled by existing `video.video_url` unique constraint — re-running is inherently idempotent.

## 7. Orchestration Flow

### `runner.py` — entrypoint

```
1. Load config from YAML
2. Validate config (fail fast if invalid)
3. Open DB session
4. Call service.run_once(session, config)
5. Log summary to stdout
6. Return exit code: 0 = success, 1 = partial failures, 2 = total failure
```

### `service.run_once(session, config)` — orchestration

```
1. Create youtube_watch_run record (started_at = now)
2. For each channel in config.channels:
   a. Resolve channel identifier to IngestionYoutubeChannelRunRequest
   b. Call ingestion.service.ingest_youtube_channel(session, request)
   c. Map result to youtube_watch_channel_result record
   d. Accumulate counters
   e. On exception: log error, record failure, continue to next channel
3. Finalize run record (finished_at, status, counters)
4. Return JobRunSummary
```

### Per-channel flow (handled by existing `ingest_youtube_channel`)

```
1. Resolve youtube_channel_id (from config value or handle → yt-dlp lookup)
2. List recent videos via yt-dlp flat-playlist (up to video_count)
3. For each video:
   a. Check if exists in DB → skip if yes
   b. Fetch transcript from YouTube
   c. Build IngestionYoutubeRequest with video + transcript
   d. Call ingest_youtube() → full pipeline (metadata, channel, video, transcript, summary, classification)
4. Return channel-level result with counters
```

## 8. Error Handling

### Channel-level isolation

One channel failing must never crash the entire run. Each channel is wrapped in try/except:

- **Network/yt-dlp errors**: logged, channel marked `failed`, continue
- **Transcript unavailable**: individual video marked `skipped_transcript_unavailable`, channel continues
- **LLM failure**: fallback to auto-generated summary/classification (already handled in `_auto_fill_missing_analytics_for_new_video`)
- **DB errors**: rollback channel transaction, log, continue

### No retry at job level

The existing `ingest_youtube_channel` already handles per-video error isolation. The job runs daily, so transient failures will be retried on the next run (idempotent by design).

## 9. Settings

Add to `src/config.py`:

```python
WATCH_CONFIG_PATH: str = "config/watched_channels.yaml"
```

No other new env vars needed — the job reuses existing `DATABASE_URL`, `GEMINI_API_KEY`, etc.

## 10. Development Phases

### Phase 1: Config + Schemas

1. Create `config/watched_channels.yaml` with initial channel list.
2. Add config DTOs to `schemas.py` (`WatchedChannel`, `WatchConfig`, `WatchDefaults`).
3. Implement `config.py` — YAML loader with validation.
4. Add `WATCH_CONFIG_PATH` to `Settings`.
5. Unit test: config loading, validation, defaults merging.

### Phase 2: Run Log Persistence

1. Add `models.py` with `YouTubeWatchRun` and `YouTubeWatchChannelResult` SQLModel tables.
2. Register models in startup import flow (`src/main.py`).
3. Implement `repository.py` — create run, add channel result, finalize run.
4. Add migration support in `database.py` if needed.

### Phase 3: Service Orchestration

1. Implement `service.run_once()`:
   - Load config
   - Iterate channels
   - Call `ingest_youtube_channel()` per channel
   - Map results to channel result records
   - Create/finalize run record
2. Return `JobRunSummary`.
3. Integration test with mocked yt-dlp + transcript.

### Phase 4: Runner + CLI

1. Implement `runner.py`:
   - Config loading
   - DB session setup (import models, create tables)
   - Call `service.run_once()`
   - Structured logging output
   - Exit code mapping
2. Test end-to-end with `python -m src.jobs.youtube_watch.runner`.

### Phase 5: Tests

1. Config loading: valid YAML, missing file, invalid entries, defaults.
2. Service orchestration: new video detected, duplicate skipped, transcript unavailable, channel failure isolation.
3. Repository: run log creation, channel result persistence.
4. Runner: exit codes, logging output.

## 11. Test Scenarios

| # | Scenario | Expected |
|---|----------|----------|
| 1 | New video detected | Video created, transcript stored, summary + classification generated, counters correct |
| 2 | Duplicate video | Skipped, no duplicate rows, skip counter increments |
| 3 | Transcript unavailable | Video may be skipped, error logged, run continues |
| 4 | Channel yt-dlp failure | Channel marked failed, other channels still processed |
| 5 | Empty config | Run completes with 0 channels scanned |
| 6 | Invalid channel in config | Validation error at startup (fail fast) |
| 7 | LLM failure | Fallback to auto-generated summary/classification |
| 8 | Mixed outcomes across channels | Run status = `partial_fail`, individual results correct |

## 12. Deployment on Coolify

### Constraint: SQLite requires same-container execution

The API and the job **must share the same SQLite file** at `/data/tracker.db`. SQLite uses file-level locking — running a separate container against the same volume risks corruption under concurrent writes. Therefore the job runs **inside the existing API container**, not as a separate service.

### Strategy: separate Coolify service (PostgreSQL — no file-locking constraint)

Because the project uses **PostgreSQL**, the job and the API connect independently to the same DB. No file-locking concern. The job runs as a **separate Coolify service** using the same Docker image with an overridden CMD.

```
┌─────────────────────────────────────────────────────┐
│  Coolify                                            │
│                                                     │
│  tracker-api service          tracker-job service   │
│  ┌──────────────────────┐    ┌─────────────────┐    │
│  │ uvicorn src.main:app │    │ python -m       │    │
│  │ (always running)     │    │  src.jobs.      │    │
│  │                      │    │  youtube_watch. │    │
│  │                      │    │  runner         │    │
│  │                      │    │ (cron, exits)   │    │
│  └──────────────────────┘    └─────────────────┘    │
│           │                         │               │
│           └──────────┬──────────────┘               │
│                      │                              │
│              PostgreSQL service                      │
│              (tracker DB)                           │
└─────────────────────────────────────────────────────┘
```

### Coolify setup steps

1. **Deploy the API service** as normal (same Docker image, CMD unchanged, port 8000).
2. **Add a second service** from the same Docker image:
   - Override CMD: `.venv/bin/python -m src.jobs.youtube_watch.runner`
   - Set **Schedule** (cron): `0 8 * * *` (daily at 08:00 UTC)
   - Set same env vars: `DATABASE_URL`, `GEMINI_API_KEY`, `WATCH_CONFIG_PATH` (optional)
3. **Config file**: `config/watched_channels.yaml` is baked into the image at `COPY config/ config/`. Update the channel list by editing the file and redeploying.

### Dockerfile change (already applied)

```dockerfile
COPY src/ src/
COPY config/ config/   ← added
COPY main.py ./
```

### Environment variables

| Var | Required | Notes |
|-----|----------|-------|
| `DATABASE_URL` | Yes | Same Postgres URL as the API service |
| `GEMINI_API_KEY` | Recommended | LLM falls back to keyword extraction if absent |
| `WATCH_CONFIG_PATH` | No | Defaults to `config/watched_channels.yaml` |

### Monitoring

- **Exit codes**: 0 = success, 1 = partial failures, 2 = fatal failure. Coolify records these per run.
- **Stdout logs**: Structured log lines visible in Coolify's container log panel.
- **DB tables**: `youtube_watch_run` and `youtube_watch_channel_result` persist every run's outcome for diagnostics.

## 13. Acceptance Criteria

1. `python -m src.jobs.youtube_watch.runner` reads config and runs one full cycle.
2. New videos are ingested with full pipeline (transcript + summary + classification).
3. Already-ingested videos are skipped (idempotent).
4. Channel failures don't crash the run.
5. Run log persisted with per-channel results.
6. Config changes (add/remove channels) take effect on next run (after redeploy).
7. Tests cover the 8 scenarios in section 11.
8. Dockerfile copies `config/` into the image.
9. Job runs correctly via Coolify "Execute Command" scheduled task.

## 14. Immediate Next Tasks

1. Create `config/watched_channels.yaml` with real channels.
2. Update `Dockerfile` to copy `config/` directory.
3. Implement Phase 1 (config + schemas).
4. Implement Phase 2 (persistence models + repository).
5. Implement Phase 3 (service orchestration).
6. Implement Phase 4 (runner CLI).
7. Add tests and validate with `pytest`.
8. Configure Coolify scheduled task after deploy.
