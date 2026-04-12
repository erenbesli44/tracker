# YouTube Fetcher Migration — yt-dlp

**Status:** Implemented and tested  
**Date:** 2026-04-12  
**Affects:** Ingestion pipeline, video metadata, channel scanning

---

## 1. What Changed and Why

### Before (old approach)

The backend used three separate, fragile mechanisms to get YouTube data:

| Task | Old mechanism | Problem |
|---|---|---|
| Video title + channel name | YouTube oEmbed API | Very limited: no description, no `channel_id`, no duration |
| Publish date | HTML scrape of youtube.com page + regex | Breaks whenever YouTube changes their page HTML |
| Channel ID from a `@handle` | HTML scrape of channel page + regex | Same fragility |
| List channel videos | YouTube RSS feed XML | **Hard cap of 15 videos**, no description, no duration |

### After (new approach)

| Task | New mechanism | Benefit |
|---|---|---|
| All video metadata | **yt-dlp** (single call) | Returns title, channel, publish date, duration, description, channel_id, subtitle list in one shot |
| Channel ID from a `@handle` | **yt-dlp** flat-playlist | Reliable, no regex |
| List channel videos | **yt-dlp** flat-playlist | **No 15-video cap**, respects the `video_count` parameter you send |
| Transcript text | `youtube-transcript-api` (unchanged) | Purpose-built, returns segments with timestamps |

### No API or browser required

Neither yt-dlp nor youtube-transcript-api requires a YouTube Data API key or a headless browser. Both work entirely on the server.

---

## 2. What Fields the Backend Now Stores

When a video is ingested the `video` table gains richer data that was previously missing:

| Field | Type | Previously | Now |
|---|---|---|---|
| `title` | `string` | From oEmbed | From yt-dlp |
| `published_at` | `datetime` (UTC, no tz) | HTML scrape, often `null` | From yt-dlp `upload_date`, more reliable |
| `duration` | `int` (seconds) | HTML scrape, often `null` | From yt-dlp, always populated |
| `channel_id` (DB FK) | `int` | Always set | Always set (unchanged) |

> **For iOS:** `duration` is now reliably populated. You can use it to display "3:33" badges, filter short clips, etc. Previously you had to assume it might be `null`.

---

## 3. API Endpoints — No Breaking Changes

The API surface is **unchanged**. All existing request/response shapes are identical. The migration is purely internal.

### 3.1 Ingest a single video by URL (simplest)

```http
POST /ingestions/youtube/url
Content-Type: application/json

{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
}
```

The backend will:
1. Call yt-dlp → get `title`, `channel name`, `publish_date`, `duration`
2. Create or reuse the channel record
3. Auto-fetch the transcript via `youtube-transcript-api`
4. Auto-generate a summary and topic classification (LLM if available, keyword fallback otherwise)
5. Return IDs for everything created

**Response:**

```json
{
  "status": "stored",
  "channel_id": 12,
  "video_id": 45,
  "transcript_id": 45,
  "summary_id": 45,
  "classification_mentions": 3,
  "person_id": null,
  "actions": {
    "person": "skipped",
    "channel": "created",
    "video": "created",
    "transcript": "created",
    "summary": "created",
    "classification": "created"
  }
}
```

- `actions.*` tells you what happened (`"created"` / `"reused"` / `"skipped"` / `"updated"`). Use this to decide what to refresh in the UI.
- `person_id` is `null` — the backend is now channel-first.

### 3.2 Ingest a single video with full control

Use this when you want to supply your own transcript or override fields:

```http
POST /ingestions/youtube
Content-Type: application/json

{
  "video": {
    "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "title": "My override title",          // optional, omit to use yt-dlp value
    "published_at": "2024-10-25T00:00:00", // optional, omit to use yt-dlp value
    "duration": 213                         // optional, omit to use yt-dlp value
  },
  "transcript": {                          // optional — omit to auto-fetch
    "raw_text": "...",
    "language": "tr"
  },
  "transcript_languages": ["tr", "en"],    // priority order, default ["tr","en"]
  "overwrite": {
    "transcript": false,   // false = keep existing, true = replace
    "summary": true,
    "classification": true
  }
}
```

### 3.3 Ingest a whole channel (bulk)

```http
POST /ingestions/youtube/channel
Content-Type: application/json

{
  "youtube_channel": "@RickAstleyYT",
  "video_count": 10,
  "transcript_languages": ["tr", "en"]
}
```

`youtube_channel` accepts any of:
- A handle: `@RickAstleyYT`
- A full channel URL: `https://www.youtube.com/@RickAstleyYT`
- A direct channel ID: `UCuAXFkgsw1L7xaCfnd5JJOw`

`video_count` can be **1–20**. With the old RSS approach this was silently capped at 15 regardless of what you passed. That cap is now gone.

**Response:**

```json
{
  "status": "completed",
  "youtube_channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
  "requested_video_count": 10,
  "videos_detected": 10,
  "videos_ingested": 8,
  "videos_skipped_existing": 2,
  "videos_skipped_no_transcript": 0,
  "errors_count": 0,
  "results": [
    {
      "youtube_video_id": "dQw4w9WgXcQ",
      "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "status": "ingested",
      "video_id": 45
    },
    {
      "youtube_video_id": "ABC123XXXXX",
      "video_url": "https://www.youtube.com/watch?v=ABC123XXXXX",
      "status": "skipped_existing",
      "video_id": 12
    }
  ]
}
```

- `status: "partial"` if any video failed (non-zero `errors_count`). Always show the user how many were skipped/failed.
- `status: "skipped_transcript_unavailable"` means YouTube has no captions for that video — this is expected for some videos.

---

## 4. Reading Video Data After Ingestion

### Get a single video

```http
GET /videos/{video_id}
```

```json
{
  "id": 45,
  "channel_id": 12,
  "person_id": null,
  "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "video_id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up",
  "published_at": "2009-10-25T00:00:00",
  "duration": 213,
  "created_at": "2026-04-12T10:00:00"
}
```

`duration` is in **seconds**. Convert to `mm:ss` in Swift:

```swift
func formatDuration(_ seconds: Int) -> String {
    let m = seconds / 60
    let s = seconds % 60
    return String(format: "%d:%02d", m, s)
}
// formatDuration(213) → "3:33"
```

### Get a video's summary

```http
GET /videos/{video_id}/summary
```

```json
{
  "id": 45,
  "video_id": 45,
  "short_summary": "...",
  "long_summary": "...",
  "highlights": ["point 1", "point 2"],
  "language": "tr",
  "source": "llm"
}
```

`highlights` is a JSON array of strings (key takeaways). Render as bullet points.

### Get a video's classification

```http
GET /videos/{video_id}/classification
```

```json
{
  "video_id": 45,
  "total_mentions": 3,
  "topic_mentions": [
    {
      "id": 1,
      "topic_id": 7,
      "topic_slug": "enflasyon",
      "summary": "...",
      "sentiment": "bearish",
      "confidence": 0.87
    }
  ]
}
```

`sentiment` is `"bullish"` / `"bearish"` / `"neutral"`.

---

## 5. Error Handling for iOS

All errors follow the same shape:

```json
{
  "detail": "Could not fetch transcript from YouTube: No transcript is available..."
}
```

| HTTP status | When it happens | iOS action |
|---|---|---|
| `200` | Success | Proceed normally |
| `422` | Invalid URL, no transcript available, bad payload | Show user a descriptive error toast |
| `502` | YouTube is unreachable from the server | Show "retry later" message |
| `500` | Internal server error | Log, show generic error |

For channel ingestion, a `200` response may still have `status: "partial"` — always check `errors_count` and show the user a summary ("8 of 10 videos imported").

---

## 6. Practical Swift Example — Ingest by URL

```swift
struct IngestByURLRequest: Encodable {
    let url: String
    let transcriptLanguages: [String]?

    enum CodingKeys: String, CodingKey {
        case url
        case transcriptLanguages = "transcript_languages"
    }
}

struct IngestionResponse: Decodable {
    let status: String
    let channelId: Int
    let videoId: Int
    let transcriptId: Int?
    let summaryId: Int?
    let classificationMentions: Int
    let actions: ActionStatus

    enum CodingKeys: String, CodingKey {
        case status
        case channelId = "channel_id"
        case videoId = "video_id"
        case transcriptId = "transcript_id"
        case summaryId = "summary_id"
        case classificationMentions = "classification_mentions"
        case actions
    }
}

struct ActionStatus: Decodable {
    let channel: String
    let video: String
    let transcript: String
    let summary: String
    let classification: String
}

// Usage
func ingestVideo(url: String) async throws -> IngestionResponse {
    let body = IngestByURLRequest(url: url, transcriptLanguages: ["tr", "en"])
    var request = URLRequest(url: URL(string: "http://your-server/ingestions/youtube/url")!)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try JSONEncoder().encode(body)

    let (data, response) = try await URLSession.shared.data(for: request)
    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
        let err = try? JSONDecoder().decode([String: String].self, from: data)
        throw AppError.serverError(err?["detail"] ?? "Unknown error")
    }
    return try JSONDecoder().decode(IngestionResponse.self, from: data)
}
```

---

## 7. Files Changed in This Migration

| File | What changed |
|---|---|
| `src/videos/service.py` | Removed `fetch_youtube_oembed_metadata()` and `fetch_youtube_publish_date()`. Added `fetch_youtube_metadata()` using yt-dlp. |
| `src/ingestion/service.py` | `_resolve_published_at()` now reads from yt-dlp metadata instead of scraping HTML. `_resolve_video()` fills `duration` from metadata. `_fetch_channel_id_from_handle()` and `_list_recent_channel_videos()` rewritten with yt-dlp. Removed `_parse_feed_datetime()`, `_extract_channel_id_from_html()`. |
| `pyproject.toml` | Added `yt-dlp` dependency |
| `tests/ingestion/test_router.py` | Mock target renamed `fetch_youtube_oembed_metadata` → `fetch_youtube_metadata` (7 patches) |
| `tests/ingestion/test_deep_pipeline.py` | Same rename (1 patch) |

---

## 8. Dependency Note

`yt-dlp` is included in the Python environment (`pyproject.toml`). No additional setup required. The Docker image will install it automatically during build.
