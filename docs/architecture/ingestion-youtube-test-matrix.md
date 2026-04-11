# Test Matrix - `POST /ingestions/youtube`

Status: Draft  
Date: 2026-04-11  
Purpose: Test scenarios for Phase 1A ingestion endpoint and Phase 1B summary persistence behavior.

## 1. Test Strategy

Test levels:

1. API integration tests (FastAPI + in-memory SQLite)
2. Service-level tests for create/reuse/overwrite logic
3. Contract tests for response structure and status codes

## 2. Base Fixture

For all ingestion tests:

1. seeded topics exist (`TOPIC_SEED`)
2. empty person/video/transcript/topic_mention/video_summary tables
3. deterministic request payload templates

## 3. Scenario Matrix

| ID | Scenario | Input Conditions | Expected Result |
|---|---|---|---|
| ING-001 | Full create | New person name, new video URL, transcript+summary+classification provided | `200`, all entities created, valid IDs returned |
| ING-002 | Reuse person by id | Existing person id provided | `200`, person reused, new video created |
| ING-003 | Reuse person by name | Existing person name provided, id omitted | `200`, person reused by normalized slug |
| ING-004 | Reuse existing video no overwrite | Existing video URL with existing transcript, `overwrite.transcript=false` | `409` conflict |
| ING-005 | Existing video with transcript overwrite | Existing transcript, `overwrite.transcript=true` | `200`, transcript updated |
| ING-006 | Existing video with summary overwrite | Existing summary, `overwrite.summary=true` | `200`, summary updated, `updated_at` set |
| ING-007 | Existing video with summary no overwrite | Existing summary, `overwrite.summary=false` | `200`, summary unchanged, action `skipped` |
| ING-008 | Classification replace | Existing mentions, `overwrite.classification=true` | `200`, old mentions removed, new mentions count returned |
| ING-009 | Classification keep | Existing mentions, `overwrite.classification=false` | `200`, mentions unchanged |
| ING-010 | Missing person id and name | `person` object has neither `id` nor `name` | `422` validation failure |
| ING-011 | Invalid YouTube URL | `video.video_url` invalid | `422` validation failure |
| ING-012 | Missing transcript | request lacks `transcript` | `422` validation failure |
| ING-013 | Invalid topic_id in classification | unknown topic id in mention | `422` (or `404`, depending final contract) |
| ING-014 | Empty short summary | summary object present with empty `short_summary` | `422` validation failure |
| ING-015 | Minimal payload | person+video+transcript only | `200`, summary/classification skipped |
| ING-016 | Idempotent replay with same payload | same request replayed with same `request_id` | same stable response, no duplicates |
| ING-017 | No request_id replay | same payload replay without request_id | no duplicate video row due to URL uniqueness |
| ING-018 | Large transcript boundary | transcript near allowed size | `200`, persisted without truncation |
| ING-019 | Unicode/TR text integrity | Turkish characters in transcript and summary | `200`, content round-trips correctly |
| ING-020 | Timestamp optionality | no `published_at` | `200`, video stored with null published time |

## 4. Assertions Per Scenario

Core assertions:

1. HTTP status code
2. response schema shape
3. DB row counts before/after
4. entity linkage integrity:
1. `video.person_id` points to expected person
2. `transcript.video_id` unique
3. `video_summary.video_id` unique
4. `topic_mention.video_id/person_id/topic_id` valid
5. overwrite behavior correctness

## 5. Negative Cases for Data Integrity

1. classification payload with empty `topic_mentions` -> `422`
2. invalid sentiment value -> `422`
3. invalid confidence range (<0 or >1) -> `422`
4. invalid language length (>10) -> `422`

## 6. Suggested Test File Layout

1. `tests/ingestion/test_router.py`
2. `tests/ingestion/test_idempotency.py`
3. `tests/summaries/test_router.py`

## 7. Phase 2 Extension Cases (LLM Endpoints)

Keep separate from Phase 1 suite:

1. `LLM_ENABLED=false` and `use_llm=true` -> blocked with explicit error
2. `dry_run=true` -> no external call, cost estimate returned
3. transcript hash unchanged -> cached result returned, no provider call
4. request/day cap exceeded -> blocked with budget error

## 8. CI Gate Criteria

1. All ingestion and summary tests pass.
2. No duplicate video rows for replayed payloads.
3. Overwrite flags behave exactly as contract.
4. Timeline endpoints remain correct after ingestion-created mentions.

