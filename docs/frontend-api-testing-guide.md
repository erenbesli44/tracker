# Frontend API Testing Guide (Temporary UI)

This guide is for building a simple test UI against the current API.
It is not the final product spec.

## 1) Run the API locally

```bash
uv run uvicorn src.main:app --reload
```

Default local base URL:

```text
http://127.0.0.1:8000
```

Quick checks:

- Health: `GET /health`
- Swagger UI: `GET /docs`
- OpenAPI JSON: `GET /openapi.json`

## 2) API flow for frontend tests

Use this order in your temporary UI:

1. Create person
2. Fetch topics (pick topic id)
3. Create video for person
4. Upload transcript
5. Submit classification
6. Read classification
7. Read timeline and overview

## 3) Minimal endpoint map

### Persons

- `POST /persons/` create person
- `GET /persons/` list persons
- `GET /persons/{person_id}` get person detail
- `PATCH /persons/{person_id}` update person

### Topics

- `GET /topics/` list topics
- `GET /topics/tree` list topics as hierarchy
- `POST /topics/` create topic
- `GET /topics/{topic_id}` get topic detail

### Videos + Transcript

- `POST /videos/` create video
- `GET /videos/?person_id={id}` list videos by person
- `GET /videos/{video_id}` get video (+ transcript meta)
- `GET /videos/{video_id}/transcript` get stored transcript (includes raw text)
- `POST /videos/{video_id}/transcript` upload transcript
- `POST /videos/{video_id}/transcript/fetch` fetch transcript from YouTube and store
- `POST /videos/{video_id}/summary` create or update summary
- `GET /videos/{video_id}/summary` get stored summary

### Ingestion

- `POST /ingestions/youtube` ingest person + video + transcript + optional summary/classification

### Classification

- `POST /videos/{video_id}/classify` submit topic mentions
- `GET /videos/{video_id}/classification` get topic mentions
- `GET /videos/classification/prompt` get prompt template

### Timeline

- `GET /persons/{person_id}/topics/overview`
- `GET /persons/{person_id}/topics/{topic_id}/timeline?limit=50&offset=0`

## 4) Copy/paste request payloads

### Create person

```json
{
  "name": "Taner Genek",
  "platform": "youtube",
  "platform_handle": "@tanergenek",
  "bio": "Temporary test profile"
}
```

### Create video

```json
{
  "person_id": 1,
  "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw",
  "title": "Test Video",
  "published_at": "2025-01-15T10:00:00",
  "duration": 1200
}
```

### Upload transcript

```json
{
  "raw_text": "BIST100 için 13100 ve 12900 kritik seviyeler.",
  "language": "tr"
}
```

### Fetch transcript from YouTube

```json
{
  "languages": ["tr", "en"],
  "overwrite_existing": false
}
```

### Submit classification

```json
{
  "topic_mentions": [
    {
      "topic_id": 5,
      "summary": "BIST100 tarafında aşağı yönlü risk devam ediyor.",
      "sentiment": "bearish",
      "key_levels": ["13100", "12900"],
      "start_time": "00:01:00",
      "end_time": "00:05:00",
      "confidence": 0.9
    }
  ]
}
```

### Upsert summary

```json
{
  "short_summary": "Kisa ozet",
  "long_summary": "Detayli ozet",
  "highlights": ["Madde 1", "Madde 2"],
  "language": "tr",
  "source": "manual"
}
```

### One-shot ingestion

```json
{
  "person": { "name": "Taner Genek" },
  "video": { "video_url": "https://www.youtube.com/watch?v=bnk8WqphWMw" },
  "transcript": { "raw_text": "Transkript metni.", "language": "tr" },
  "summary": { "short_summary": "Kisa ozet" },
  "classification": {
    "topic_mentions": [
      { "topic_id": 5, "summary": "BIST gorunumu zayif", "sentiment": "bearish" }
    ]
  },
  "overwrite": { "transcript": true, "summary": true, "classification": true }
}
```

## 5) Browser `fetch` template for a quick test UI

```js
const API = "http://127.0.0.1:8000";

async function req(path, options = {}) {
  const res = await fetch(`${API}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${res.status} ${JSON.stringify(body)}`);
  return body;
}

// Example end-to-end test
async function runSmokeTest() {
  const person = await req("/persons/", {
    method: "POST",
    body: JSON.stringify({ name: "UI Test Person" })
  });

  const topics = await req("/topics/");
  const borsa = topics.find(t => t.slug === "borsa-istanbul");

  const video = await req("/videos/", {
    method: "POST",
    body: JSON.stringify({
      person_id: person.id,
      video_url: `https://www.youtube.com/watch?v=${Math.random().toString(36).slice(2, 13).padEnd(11, "a")}`
    })
  });

  await req(`/videos/${video.id}/transcript`, {
    method: "POST",
    body: JSON.stringify({ raw_text: "UI test transcript.", language: "tr" })
  });

  await req(`/videos/${video.id}/classify`, {
    method: "POST",
    body: JSON.stringify({
      topic_mentions: [
        {
          topic_id: borsa.id,
          summary: "Test mention",
          sentiment: "neutral",
          key_levels: ["13100"],
          confidence: 0.8
        }
      ]
    })
  });

  const classification = await req(`/videos/${video.id}/classification`);
  const timeline = await req(`/persons/${person.id}/topics/${borsa.id}/timeline`);
  const overview = await req(`/persons/${person.id}/topics/overview`);

  return { person, video, classification, timeline, overview };
}
```

## 6) Common error codes to handle in temporary UI

- `404` not found (`Person not found`, `Video not found`, `Topic not found`)
- `409` conflict (duplicate person slug, duplicate video URL, transcript already exists)
- `422` validation or invalid YouTube URL

## 7) Notes for test-only frontend

- No auth is required in current API.
- Transcript `raw_text` is accepted on upload, but transcript text is not returned in `GET /videos/{video_id}` (only transcript metadata).
- Re-classification replaces existing mentions for the same video (`POST /videos/{video_id}/classify` is overwrite behavior).
