# SwiftUI iOS API Guide (Channel-Based v2)

Status: Current backend contract
Date: 2026-04-11

This guide is for iOS developers integrating with the current API after the channel-based migration.

## 1. Migration Summary (What Changed)

The backend is now channel-first for YouTube tracking.

Old model (v1 mindset):
- Person -> Videos -> Transcript/Summary/Classification

New model (v2 current):
- YouTube Channel -> Videos -> Transcript/Summary/Classification

Compatibility behavior still exists:
- `person` tables/routes still work.
- `video` and `topic_mention` still contain `person_id` for staged compatibility.
- New canonical tracking path for iOS should use `channel_id`.

Key API changes:
1. New `youtube_channel` domain and `/channels` endpoints.
2. `VideoResponse` now includes `channel_id`.
3. `TopicMentionResponse` now includes `channel_id`.
4. Timeline responses now include both `channel_id` and optional `person_id`.
5. New channel timeline endpoints:
   - `GET /channels/{channel_id}/topics/overview`
   - `GET /channels/{channel_id}/topics/{topic_id}/timeline`
6. Ingestion response now returns `channel_id` and `actions.channel`.

## 2. Base Setup

Run API locally:

```bash
uv run uvicorn src.main:app --reload
```

Base URL:

```text
http://127.0.0.1:8000
```

Docs (local/development):
- `GET /docs`
- `GET /openapi.json`

## 3. API Conventions

- Content-Type: `application/json`
- Auth: none (current phase)
- IDs: integer
- Datetime fields are ISO-like strings (often naive UTC, no timezone suffix)
- Validation errors follow FastAPI `422` shape

Typical error shape:

```json
{ "detail": "Channel not found" }
```

Validation error shape:

```json
{
  "detail": [
    {
      "loc": ["body", "video", "video_url"],
      "msg": "Field required",
      "type": "missing"
    }
  ]
}
```

## 4. Core Data Contracts (iOS-facing)

### ChannelResponse

```json
{
  "id": 1,
  "name": "Taner Genek",
  "slug": "taner-genek",
  "platform": "youtube",
  "channel_handle": "@tanergenek",
  "youtube_channel_id": null,
  "channel_url": "https://www.youtube.com/@tanergenek",
  "bio": null,
  "legacy_person_id": 1,
  "created_at": "2026-04-11T14:00:00",
  "updated_at": null
}
```

### VideoResponse

```json
{
  "id": 10,
  "person_id": 1,
  "channel_id": 1,
  "platform": "youtube",
  "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
  "video_id": "bnk8WqphWMw",
  "title": "Video title",
  "published_at": "2025-01-15T10:00:00",
  "duration": 1200,
  "created_at": "2026-04-11T14:02:00"
}
```

### ClassificationResponse (TopicMentionResponse includes `channel_id`)

```json
{
  "video_id": 10,
  "total_mentions": 1,
  "mentions": [
    {
      "id": 99,
      "video_id": 10,
      "channel_id": 1,
      "person_id": 1,
      "topic_id": 5,
      "summary": "BIST tarafinda baski suruyor.",
      "sentiment": "bearish",
      "key_levels": ["13100", "12900"],
      "start_time": "00:01:00",
      "end_time": "00:05:00",
      "confidence": 0.9
    }
  ]
}
```

## 5. Recommended iOS Flow (Channel-First)

For channel tracking screens:
1. `GET /channels/`
2. `GET /videos/?channel_id={channelId}`
3. `GET /videos/{video_id}/summary`
4. `GET /videos/{video_id}/classification`
5. `GET /channels/{channel_id}/topics/overview`
6. `GET /channels/{channel_id}/topics/{topic_id}/timeline`

Optional admin/manual ingestion flow:
1. `POST /ingestions/youtube`
2. refresh list with `GET /videos/?channel_id={channelId}`

## 6. Endpoint Reference

## 6.1 Health

### `GET /health`

Use for app/backend liveness check.

Success `200`:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## 6.2 Channels

### `POST /channels/`

Create channel.

Request:

```json
{
  "name": "Atilla Yesilada",
  "platform": "youtube",
  "channel_handle": "@atillayesilada",
  "youtube_channel_id": null,
  "channel_url": "https://www.youtube.com/@atillayesilada",
  "bio": "Macro analysis",
  "legacy_person_id": null
}
```

Responses:
- `201` created, returns `ChannelResponse`
- `409` duplicate slug/name

### `GET /channels/`

List channels.

Responses:
- `200` list of `ChannelResponse`

### `GET /channels/{channel_id}`

Get single channel.

Responses:
- `200`
- `404` channel not found

### `PATCH /channels/{channel_id}`

Update channel fields (`name`, `channel_handle`, `youtube_channel_id`, `channel_url`, `bio`, `platform`).

Responses:
- `200`
- `404`

## 6.3 Videos

### `POST /videos/`

Create a video.

Rules:
- Must include at least one owner: `channel_id` or `person_id`.
- Channel-first for iOS: prefer `channel_id`.
- If only `person_id` is sent, backend auto-links/creates channel.
- If only `channel_id` is sent, backend resolves/creates legacy person link.

Request (recommended):

```json
{
  "channel_id": 1,
  "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
  "title": "Market update",
  "published_at": "2025-01-15T10:00:00",
  "duration": 1200
}
```

Responses:
- `201` returns `VideoResponse`
- `409` duplicate URL/video id
- `422` invalid YouTube URL
- `404` if owner id not found

### `GET /videos/`

Query options:
- `channel_id` (recommended)
- `person_id` (legacy)

Examples:
- `GET /videos/?channel_id=1`
- `GET /videos/?person_id=1`
- `GET /videos/` (all)

Response `200`: list of `VideoResponse`, sorted by `published_at desc nulls last`.

### `GET /videos/{video_id}`

Returns video + lightweight transcript metadata if present.

Response model: `VideoWithTranscript`.

Responses:
- `200`
- `404`

## 6.4 Transcript

### `POST /videos/{video_id}/transcript`

Manual transcript add.

Request:

```json
{
  "raw_text": "Transcript text...",
  "language": "tr"
}
```

Responses:
- `201`
- `409` transcript already exists
- `404` video not found

### `GET /videos/{video_id}/transcript`

Fetch full transcript text.

Responses:
- `200` (`TranscriptDetailResponse` includes `raw_text`)
- `404`

### `POST /videos/{video_id}/transcript/fetch`

Fetch transcript from YouTube via provider and store.

Request:

```json
{
  "languages": ["tr", "en"],
  "overwrite_existing": false
}
```

Request body can be empty `{}`.

Responses:
- `200` returns `TranscriptFetchResponse`
- `409` transcript already exists and overwrite is false
- `422` transcript unavailable or no valid transcript
- `502` provider failure

Success sample:

```json
{
  "transcript": {
    "id": 15,
    "video_id": 10,
    "language": "tr",
    "created_at": "2026-04-11T14:10:00",
    "raw_text": "YouTube transcript text"
  },
  "source": "youtube",
  "fetched_language": "tr",
  "is_generated": false,
  "segment_count": 123,
  "languages_tried": ["tr", "en"]
}
```

## 6.5 Summary

### `POST /videos/{video_id}/summary`

Upsert summary (create or replace).

Request:

```json
{
  "short_summary": "Kisa ozet",
  "long_summary": "Detayli ozet",
  "highlights": ["Madde 1", "Madde 2"],
  "language": "tr",
  "source": "manual"
}
```

Responses:
- `200` `VideoSummaryResponse`
- `404` video not found

### `GET /videos/{video_id}/summary`

Responses:
- `200`
- `404` summary not found

## 6.6 Classification

### `POST /videos/{video_id}/classify`

Submit classification; replaces old mentions for the same video.

Request:

```json
{
  "topic_mentions": [
    {
      "topic_id": 5,
      "summary": "BIST tarafinda baski suruyor.",
      "sentiment": "bearish",
      "key_levels": ["13100", "12900"],
      "start_time": "00:01:00",
      "end_time": "00:05:00",
      "confidence": 0.9
    }
  ]
}
```

Responses:
- `201` `ClassificationResponse`
- `404` video not found
- `422` validation errors

### `GET /videos/{video_id}/classification`

Responses:
- `200`
- `404` no classification found

### `GET /videos/classification/prompt`

Returns backend prompt template for external/manual LLM pipelines.

Responses:
- `200`

## 6.7 Timeline (Read API for trend UI)

Channel-first endpoints (recommended):

### `GET /channels/{channel_id}/topics/overview`

Returns per-topic aggregate for the channel.

Response `200` (`PersonOverviewResponse`):

```json
{
  "channel_id": 1,
  "person_id": 1,
  "topics": [
    {
      "topic": {
        "id": 5,
        "name": "Borsa Istanbul",
        "slug": "borsa-istanbul",
        "parent_id": null,
        "parent_name": null
      },
      "mention_count": 4,
      "latest_sentiment": "bearish",
      "latest_summary": "Baski suruyor.",
      "latest_published_at": "2025-01-15T10:00:00",
      "latest_video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"
    }
  ]
}
```

### `GET /channels/{channel_id}/topics/{topic_id}/timeline?limit=50&offset=0`

Responses:
- `200` `TimelineResponse`
- `404` for invalid topic or channel

Legacy-compatible person endpoints remain:
- `GET /persons/{person_id}/topics/overview`
- `GET /persons/{person_id}/topics/{topic_id}/timeline`

## 6.8 Ingestion (single-call write path)

### `POST /ingestions/youtube`

Creates/reuses person, channel, video, transcript and optional summary/classification.

Request:

```json
{
  "person": {
    "name": "Taner Genek"
  },
  "video": {
    "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
    "title": "Ingestion test"
  },
  "transcript": {
    "raw_text": "Transcript text...",
    "language": "tr"
  },
  "summary": {
    "short_summary": "Kisa ozet",
    "long_summary": "Detayli ozet",
    "highlights": ["Madde 1"],
    "source": "manual"
  },
  "classification": {
    "source": "manual",
    "topic_mentions": [
      {
        "topic_id": 5,
        "summary": "BIST tarafinda baski suruyor.",
        "sentiment": "bearish",
        "confidence": 0.9
      }
    ]
  },
  "overwrite": {
    "transcript": true,
    "summary": true,
    "classification": true
  },
  "request_id": "optional-idempotency-key"
}
```

Success response:

```json
{
  "status": "stored",
  "person_id": 1,
  "channel_id": 1,
  "video_id": 10,
  "transcript_id": 10,
  "summary_id": 10,
  "classification_mentions": 1,
  "actions": {
    "person": "created",
    "channel": "created",
    "video": "created",
    "transcript": "created",
    "summary": "created",
    "classification": "created"
  }
}
```

Responses:
- `200`
- `409` transcript exists and overwrite false
- `422` invalid topic ids, invalid YouTube URL, or missing person fallback when metadata is unavailable

## 6.9 Topics

### `GET /topics/`

List all topics.

### `GET /topics/tree`

Tree for grouped picker UI.

### `GET /topics/{topic_id}`

Single topic detail.

### `POST /topics/`

Create topic (admin/dev usage).

## 6.10 Persons (Legacy-Compatible)

`/persons` endpoints remain available for compatibility:
- `POST /persons/`
- `GET /persons/`
- `GET /persons/{person_id}`
- `PATCH /persons/{person_id}`

Note for iOS:
- New screens should prefer `/channels` and channel timelines.
- Keep `person_id` decoding in models for compatibility during migration.

## 7. iOS Model Update Checklist

1. Add `ChannelResponse` model and channel list screen source from `/channels/`.
2. Update `VideoResponse` decoding to include nullable `channel_id`.
3. Update `TopicMentionResponse` decoding to include nullable `channel_id`.
4. Use channel endpoints for overview/timeline screens.
5. Keep `person_id` fields in models until compatibility layer is retired.

## 8. Minimal Endpoint Set for First iOS Build

If you want minimal implementation first:
1. `GET /channels/`
2. `GET /videos/?channel_id={id}`
3. `GET /videos/{video_id}/summary`
4. `GET /videos/{video_id}/classification`
5. `GET /channels/{channel_id}/topics/overview`
6. `GET /channels/{channel_id}/topics/{topic_id}/timeline`

This is enough for channel list, video list, detail, and trend screens.
