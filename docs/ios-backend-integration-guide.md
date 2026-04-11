# iOS Backend Integration Guide (Current State)

Status: Implemented and tested
Date: 2026-04-11

This guide explains:
1. What changed in backend so far.
2. How iOS should call the backend now.
3. The minimum API flow to build and test the app.

## 1) What Changed So Far

Backend moved to **YouTube channel-based tracking**.

Current core model:
- `youtube_channel` -> `video` -> `transcript` / `video_summary` / `topic_mention`

Important migration updates:
- `video.person_id` is now optional (`null` allowed).
- `topic_mention.person_id` is now optional (`null` allowed).
- Channel is the primary owner (`channel_id`).
- Person routes still exist for compatibility, but **not required** for channel-first iOS flow.

### New/updated behavior
- `/channels/` endpoints are active.
- `POST /videos/` works with `channel_id` only.
- `POST /ingestions/youtube` works without `person`; backend resolves/creates channel and stores with `person_id = null` when person not provided.
- Transcript fetch from YouTube is implemented at:
  - `POST /videos/{video_id}/transcript/fetch`

## 2) Backend Run (Local)

Run backend:

```bash
uv run uvicorn src.main:app --reload
```

Base URL:

```text
http://127.0.0.1:8000
```

OpenAPI:
- `GET /docs`
- `GET /openapi.json`

## 3) iOS Recommended API Flow (Channel-First)

For app tabs/screens:
1. `GET /channels/` -> channel list
2. `GET /videos/?channel_id={channelId}` -> channel video feed
3. `GET /videos/{video_id}/summary` -> summary section
4. `GET /videos/{video_id}/classification` -> per-video topic mentions
5. `GET /channels/{channel_id}/topics/overview` -> topic cards/overview
6. `GET /channels/{channel_id}/topics/{topic_id}/timeline` -> topic history chart/list

For manual/admin ingest tools:
1. `POST /ingestions/youtube`
2. optionally `POST /videos/{video_id}/transcript/fetch`

## 4) Endpoint Usage

## 4.1 Channels

### List channels
`GET /channels/`

Response:

```json
[
  {
    "id": 1,
    "name": "Taner Genek",
    "slug": "taner-genek",
    "platform": "youtube",
    "channel_handle": "@tanergenek",
    "youtube_channel_id": null,
    "channel_url": null,
    "bio": null,
    "legacy_person_id": null,
    "created_at": "2026-04-11T14:00:00",
    "updated_at": null
  }
]
```

### Create channel
`POST /channels/`

```json
{
  "name": "Taner Genek",
  "platform": "youtube",
  "channel_handle": "@tanergenek"
}
```

## 4.2 Videos

### Create video (channel-only)
`POST /videos/`

```json
{
  "channel_id": 1,
  "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
  "title": "Video title",
  "published_at": "2025-01-15T10:00:00",
  "duration": 1200
}
```

### List videos by channel
`GET /videos/?channel_id=1`

### Get video detail
`GET /videos/{video_id}`

## 4.3 Transcript

### Add transcript manually
`POST /videos/{video_id}/transcript`

```json
{
  "raw_text": "Transcript text...",
  "language": "tr"
}
```

### Fetch transcript from YouTube provider
`POST /videos/{video_id}/transcript/fetch`

```json
{
  "languages": ["tr", "en"],
  "overwrite_existing": false
}
```

## 4.4 Summary

### Upsert summary
`POST /videos/{video_id}/summary`

```json
{
  "short_summary": "Kisa ozet",
  "long_summary": "Detayli ozet",
  "highlights": ["Madde 1", "Madde 2"],
  "language": "tr",
  "source": "manual"
}
```

### Get summary
`GET /videos/{video_id}/summary`

## 4.5 Classification

### Submit classification (replace-on-write)
`POST /videos/{video_id}/classify`

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

### Get classification
`GET /videos/{video_id}/classification`

## 4.6 Timeline (channel-based)

### Channel topic overview
`GET /channels/{channel_id}/topics/overview`

### Channel topic timeline
`GET /channels/{channel_id}/topics/{topic_id}/timeline?limit=50&offset=0`

## 4.7 Ingestion

### Ingest one YouTube video (channel-first)
`POST /ingestions/youtube`

Minimal request (no person required):

```json
{
  "video": {
    "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw"
  },
  "transcript": {
    "raw_text": "Transcript text...",
    "language": "tr"
  },
  "overwrite": {
    "transcript": true,
    "summary": true,
    "classification": true
  }
}
```

Response (person can be null):

```json
{
  "status": "stored",
  "person_id": null,
  "channel_id": 1,
  "video_id": 10,
  "transcript_id": 10,
  "summary_id": null,
  "classification_mentions": 0,
  "actions": {
    "person": "skipped",
    "channel": "created",
    "video": "created",
    "transcript": "created",
    "summary": "skipped",
    "classification": "skipped"
  }
}
```

### Ingest latest N videos from channel
`POST /ingestions/youtube/channel`

```json
{
  "youtube_channel": "@tanergenek",
  "video_count": 3,
  "transcript_languages": ["tr", "en"]
}
```

## 5) Error Codes iOS Should Handle

- `404`: resource not found
- `409`: conflict (duplicate URL, transcript already exists, channel-person mismatch)
- `422`: validation or unavailable transcript
- `502`: YouTube transcript provider failure

Common shape:

```json
{ "detail": "..." }
```

Validation shape:

```json
{
  "detail": [
    {
      "loc": ["body", "field"],
      "msg": "...",
      "type": "..."
    }
  ]
}
```

## 6) iOS Model Notes

Keep these fields nullable in Swift models:
- `VideoResponse.person_id`
- `TopicMentionResponse.person_id`
- `legacy_person_id` on channel

Keep datetime as String first if you want safer decoding across environments.

## 7) Quick Smoke Checklist

1. `GET /channels/` returns `200` and array.
2. Create channel + create video with `channel_id` only.
3. Add/fetch transcript.
4. Upsert/get summary.
5. Classify/get classification.
6. Load channel topic overview/timeline.

If these pass, iOS channel-first flow is correctly wired.
