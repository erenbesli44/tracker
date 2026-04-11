# Formal Development Plan - Manual Ingestion + Cost-Controlled LLM

Status: Draft (planning only, no implementation in this document)  
Date: 2026-04-11  
Owner: Backend/API

## 1. Objective

Define a phased plan where:

- Phase 1 stores influencer, video, transcript, summary, and classification through API calls.
- Phase 1 has no automatic social media listening or background triggers.
- Gemini usage is optional, manual, and cost-controlled.

## 2. Scope and Non-Goals

### In scope

- API contracts for manual ingestion and storage.
- Database extensions for transcript summary persistence.
- Optional LLM execution endpoints for a later phase.
- Cost guardrails for Gemini usage.

### Out of scope

- Automatic crawling/listening of YouTube or other platforms.
- Event-driven auto pipelines that trigger summarize/classify.
- Production-grade multi-tenant authorization design.

## 3. Current Baseline (verified from code)

### Existing API capabilities

- Persons CRUD.
- Topics list/tree/create/get (with startup seed).
- Videos create/list/get.
- Transcript upload per video.
- Classification submit/get (replace-on-write).
- Timeline overview and topic timeline.

### Existing database tables

- `person`
- `video`
- `transcript`
- `topic`
- `topic_mention`

### Existing timeline indexes

- `(person_id, topic_id, created_at)` on `topic_mention`
- `(video_id, topic_id)` on `topic_mention`

## 4. Target Architecture by Phase

## Phase 1A - Manual First Ingestion (No LLM Calls)

Goal: one API call stores all available data for a given influencer and video.

### New endpoint (planned)

`POST /ingestions/youtube`

### Planned behavior

- Accept manual payload containing person, video, transcript, optional summary, and optional classification.
- Perform create-or-reuse logic:
  - person by `id` (if provided) or normalized slug/name.
  - video by unique `video_url`.
  - transcript create/overwrite based on explicit flag.
  - summary create/overwrite based on explicit flag.
  - classification replace/keep based on explicit flag.
- Return normalized IDs and stored entity references for frontend confirmation.

### Example request

```json
{
  "person": {
    "id": 1,
    "name": "Taner Genek",
    "platform": "youtube",
    "platform_handle": "@tanergenek"
  },
  "video": {
    "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
    "title": "Test Video",
    "published_at": "2025-01-15T10:00:00",
    "duration": 1200
  },
  "transcript": {
    "raw_text": "Transcript text...",
    "language": "tr"
  },
  "summary": {
    "short_summary": "Kisa ozet",
    "long_summary": "Detayli ozet",
    "highlights": ["Madde 1", "Madde 2"],
    "source": "manual"
  },
  "classification": {
    "source": "manual",
    "topic_mentions": [
      {
        "topic_id": 5,
        "summary": "BIST tarafinda dusus riski suruyor.",
        "sentiment": "bearish",
        "key_levels": ["13100", "12900"],
        "start_time": "00:01:00",
        "end_time": "00:05:00",
        "confidence": 0.9
      }
    ]
  },
  "overwrite": {
    "transcript": false,
    "summary": true,
    "classification": true
  },
  "request_id": "optional-idempotency-key"
}
```

### Example response

```json
{
  "person_id": 1,
  "video_id": 10,
  "transcript_id": 10,
  "summary_id": 10,
  "classification_mentions": 3,
  "status": "stored"
}
```

## Phase 1B - Summary Persistence

Goal: store summary as first-class data, independent from classification.

### Database addition (planned)

Table: `video_summary` (1:1 with `video`)

Columns:

1. `id` (PK)
2. `video_id` (FK -> `video.id`, unique, indexed)
3. `short_summary` (TEXT, required)
4. `long_summary` (TEXT, nullable)
5. `highlights` (TEXT, JSON array as string)
6. `language` (VARCHAR(10), default `tr`)
7. `source` (VARCHAR(20): `manual` or `llm`)
8. `created_at` (DATETIME)
9. `updated_at` (DATETIME, nullable)

### Endpoints (planned)

- `POST /videos/{video_id}/summary` create/replace summary.
- `GET /videos/{video_id}/summary` fetch summary.

## Phase 1C - Frontend Test Readiness

Goal: make temporary UI testing straightforward and deterministic.

### Tasks

- Keep existing endpoints backward compatible.
- Use the frontend testing guide for temporary UI flow.
- Add tests for:
  - `POST /ingestions/youtube`
  - summary create/get
  - overwrite policy behavior
  - error and validation cases
- Ensure no dependency on dummy sample data (except taxonomy seed).

## Phase 2 - Optional Gemini LLM Execution (Manual Trigger)

Goal: allow summarize/classify using Gemini only when explicitly requested.

### Planned endpoints

- `POST /videos/{video_id}/summarize:run`
- `POST /videos/{video_id}/classify:run`
- Optional combined endpoint: `POST /videos/{video_id}/analyze:run`

### Required cost controls

- `LLM_ENABLED=false` by default.
- Request must include `use_llm=true`.
- Transcript hash cache check:
  - if transcript is unchanged and output exists, return cached output.
  - skip external Gemini call.
- Budget limits:
  - max characters per request
  - max requests per day
  - optional monthly spend cap
- `dry_run=true` mode:
  - estimate token/cost only
  - no Gemini API call
- Persist run metadata:
  - provider/model
  - input/output token counts (if available)
  - estimated cost
  - run status and timestamps

## 5. Data Contracts and Storage Rules

- Classification remains replace-on-write for the same `video_id`.
- Summary is a single current version per video in Phase 1 (no history table).
- Transcript is stored once per video unless explicit overwrite is requested.
- Timeline continues to read from `topic_mention` and `video`.

## 6. Validation and Error Strategy

### Existing status code semantics

- `404` not found
- `409` conflict
- `422` validation failure or invalid YouTube URL

### Planned ingestion validations

- Require `video.video_url`.
- Require either `person.id` or `person.name`.
- If classification is provided, validate topic existence.
- If summary is provided, require non-empty `short_summary`.

## 7. Security and Operations (Phase 1 Level)

- No automatic job runner.
- LLM key loaded only from environment variables.
- Log LLM attempts and guard-based skips.
- Keep OpenAPI available in local/development for frontend testing.

## 8. Milestones

1. M1: API contract freeze for ingestion and summary.
2. M2: Schema update (`video_summary`) and migration approach.
3. M3: Implement ingestion endpoint and summary endpoints.
4. M4: Add test coverage and update docs.
5. M5: Add manual-trigger Gemini endpoints behind feature flag.
6. M6: Add cost telemetry and dry-run mode.

## 9. Acceptance Criteria

- A single manual API call can store influencer, video, transcript, optional summary, and optional classification.
- Frontend can test complete flow without LLM usage.
- Repeated ingestion does not create duplicate videos for the same URL.
- Gemini calls never execute unless both conditions are true:
  - feature flag is enabled
  - request explicitly asks for LLM run
- Timeline and overview remain correct after ingestion.

## 10. Risks and Mitigations

- Risk: duplicate or low-quality manual input.
  - Mitigation: idempotency strategy and explicit overwrite flags.
- Risk: uncontrolled LLM spend.
  - Mitigation: default-off, hard limits, cache, and dry-run mode.
- Risk: schema drift between docs and code.
  - Mitigation: keep endpoint contracts and tests synchronized.

## 11. Immediate Next Planning Artifacts

After approval of this plan, prepare:

1. OpenAPI contract draft for `POST /ingestions/youtube`.
2. SQLModel schema draft for `video_summary`.
3. Test matrix (happy path, overwrite, conflicts, validation failures).
