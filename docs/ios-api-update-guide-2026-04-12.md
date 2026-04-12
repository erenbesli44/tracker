# iOS Developer Guide: API Updates (2026-04-12)

Status: Implemented
Date: 2026-04-12

Covers all backend changes since the PostgreSQL migration. Includes new endpoints, updated schemas, and Swift integration examples.

---

## Summary of Changes

| Change | Type | Breaking? |
|---|---|---|
| `GET /topics/{slug}/opinions` — cross-channel opinions | New endpoint | No |
| `POST /ingestions/youtube/url` — simple URL ingestion | New endpoint | No |
| Channel topic focus fields (`primary_topic_slug`, `expected_subtopics`) | New fields | No (nullable) |
| PostgreSQL migration | Infrastructure | No (same API) |
| YouTube fetcher migration (yt-dlp) | Internal | No |

---

## 1. New Endpoint: Cross-Channel Topic Opinions

**Purpose**: Given a subtopic (e.g. `dolar`), returns what every tracked channel says about it — grouped by channel with their latest opinions, sentiments, and key price levels.

### Request

```
GET /topics/{topic_slug}/opinions?limit=5&days=30
```

| Parameter | In | Type | Default | Description |
|---|---|---|---|---|
| `topic_slug` | path | `string` | required | Subtopic slug (e.g. `dolar`, `altin`, `borsa-istanbul`) |
| `limit` | query | `int` (1-50) | `5` | Max opinion entries per channel |
| `days` | query | `int` (1-365) or null | `30` | Lookback window in days. Omit or pass `null` for all-time |

### Response (200)

```json
{
  "topic": {
    "id": 8,
    "name": "Dolar",
    "slug": "dolar",
    "parent_id": 1,
    "description": "USD/TRY, doviz, kur",
    "created_at": "2026-04-11T11:50:18"
  },
  "total_channels": 2,
  "channel_opinions": [
    {
      "channel_id": 1,
      "channel_name": "Atilla Yesilada",
      "channel_slug": "atilla-yesilada",
      "channel_handle": "@atillayesilada",
      "mention_count": 12,
      "latest_sentiment": "bearish",
      "entries": [
        {
          "mention_id": 42,
          "video_id": 10,
          "video_title": "Dolar nereye gidiyor?",
          "video_url": "https://www.youtube.com/watch?v=abc123",
          "published_at": "2026-04-10T14:00:00",
          "summary": "Dolar kisa vadede 38 seviyesine gerileyebilir ancak jeopolitik riskler yukari yonlu baski olusturuyor.",
          "sentiment": "bearish",
          "key_levels": ["38.0", "39.5"],
          "confidence": 0.92
        },
        {
          "mention_id": 35,
          "video_id": 8,
          "video_title": "Haftalik piyasa degerlendirmesi",
          "video_url": "https://www.youtube.com/watch?v=def456",
          "published_at": "2026-04-03T10:00:00",
          "summary": "Merkez Bankasi doviz rezervlerini kullanarak TL'yi destekliyor.",
          "sentiment": "neutral",
          "key_levels": null,
          "confidence": 0.80
        }
      ]
    },
    {
      "channel_id": 3,
      "channel_name": "Taner Genek",
      "channel_slug": "taner-genek",
      "channel_handle": "@tanergenek",
      "mention_count": 5,
      "latest_sentiment": "bullish",
      "entries": [
        {
          "mention_id": 50,
          "video_id": 15,
          "video_title": "Piyasa ozeti",
          "video_url": "https://www.youtube.com/watch?v=ghi789",
          "published_at": "2026-04-09T09:00:00",
          "summary": "Dolar guclenmeye devam ediyor, 40 seviyesi hedefte.",
          "sentiment": "bullish",
          "key_levels": ["39.0", "40.0"],
          "confidence": 0.88
        }
      ]
    }
  ]
}
```

### Error Responses

| Status | Condition | Body |
|---|---|---|
| 404 | Unknown topic slug | `{"detail": "Topic not found"}` |
| 422 | Invalid query params | Validation error |

### Notes

- Channels are ordered by most recent mention (most recently active first)
- `mention_count` is the total count for that channel (may exceed `limit`)
- `latest_sentiment` is the sentiment of the most recent entry
- Topics with no mentions return `total_channels: 0` and empty `channel_opinions`
- Only channels with a `channel_id` linked to mentions are included (legacy person-only records excluded)

---

## 2. New Endpoint: Simple URL Ingestion

**Purpose**: Ingest a YouTube video with just a URL. The backend automatically fetches metadata, transcript, generates summary, and classifies topics.

### Request

```
POST /ingestions/youtube/url
```

```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "transcript_languages": ["tr", "en"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | `string` (1-500) | Yes | YouTube video URL |
| `transcript_languages` | `string[]` | No | Language priority for transcript fetch. Defaults to `["tr", "en"]` |

### Response (200)

Uses the existing `IngestionYoutubeResponse` schema:

```json
{
  "status": "stored",
  "video_id": 15,
  "person_id": null,
  "channel_id": 3,
  "transcript_id": 15,
  "summary_id": 15,
  "video_action": "created",
  "transcript_action": "created",
  "summary_action": "created",
  "classification_action": "created"
}
```

### Error Responses

| Status | Condition |
|---|---|
| 400 | Invalid YouTube URL or transcript unavailable |
| 409 | Video already ingested (returns existing data with `status: "unchanged"`) |

---

## 3. Updated Channel Schema Fields

Two new nullable fields on channel responses (see [channel-aware-classification-guide.md](channel-aware-classification-guide.md) for full details):

| Field | Type | Description |
|---|---|---|
| `primary_topic_slug` | `string?` | Main topic area (e.g. `"ekonomi"`) |
| `expected_subtopics` | `string[]?` | Subtopic slugs this channel typically covers |
| `legacy_person_id` | `int?` | Link to legacy Person record |
| `updated_at` | `datetime?` | Last update timestamp |

These affect `GET /channels`, `GET /channels/{id}`, `POST /channels`, and `PATCH /channels/{id}`.

---

## 4. Swift Models

### TopicOpinionsResponse (NEW)

```swift
struct TopicOpinionsResponse: Codable {
    let topic: TopicDetail
    let totalChannels: Int
    let channelOpinions: [ChannelOpinionGroup]
}

struct ChannelOpinionGroup: Codable, Identifiable {
    let channelId: Int
    let channelName: String
    let channelSlug: String
    let channelHandle: String?
    let mentionCount: Int
    let latestSentiment: String?
    let entries: [ChannelOpinionEntry]

    var id: Int { channelId }
}

struct ChannelOpinionEntry: Codable, Identifiable {
    let mentionId: Int
    let videoId: Int
    let videoTitle: String?
    let videoUrl: String
    let publishedAt: String?
    let summary: String
    let sentiment: String?
    let keyLevels: [String]?
    let confidence: Double

    var id: Int { mentionId }
}

struct TopicDetail: Codable {
    let id: Int
    let name: String
    let slug: String
    let parentId: Int?
    let description: String?
    let createdAt: String
}
```

### IngestByUrlRequest (NEW)

```swift
struct IngestByUrlRequest: Codable {
    let url: String
    let transcriptLanguages: [String]?

    init(url: String, transcriptLanguages: [String]? = ["tr", "en"]) {
        self.url = url
        self.transcriptLanguages = transcriptLanguages
    }
}
```

### Updated ChannelResponse

Add these optional fields to your existing `ChannelResponse` struct:

```swift
struct ChannelResponse: Codable, Identifiable {
    // ... existing fields ...
    let primaryTopicSlug: String?       // NEW
    let expectedSubtopics: [String]?    // NEW
    let legacyPersonId: Int?            // NEW
    let updatedAt: String?              // NEW
}
```

---

## 5. Swift Integration Examples

### Fetch cross-channel opinions for a subtopic

```swift
func fetchTopicOpinions(
    topicSlug: String,
    limit: Int = 5,
    days: Int? = 30
) async throws -> TopicOpinionsResponse {
    var components = URLComponents(
        url: baseURL.appendingPathComponent("topics/\(topicSlug)/opinions"),
        resolvingAgainstBaseURL: false
    )!
    var queryItems = [URLQueryItem(name: "limit", value: "\(limit)")]
    if let days {
        queryItems.append(URLQueryItem(name: "days", value: "\(days)"))
    }
    components.queryItems = queryItems

    let (data, _) = try await URLSession.shared.data(from: components.url!)
    return try JSONDecoder.snakeCase.decode(TopicOpinionsResponse.self, from: data)
}

// Usage
let opinions = try await fetchTopicOpinions(topicSlug: "dolar")
for channel in opinions.channelOpinions {
    print("\(channel.channelName): \(channel.latestSentiment ?? "N/A")")
    for entry in channel.entries {
        print("  - \(entry.summary)")
    }
}
```

### Ingest a video by URL

```swift
func ingestVideo(url: String) async throws -> IngestionYoutubeResponse {
    let endpoint = baseURL.appendingPathComponent("ingestions/youtube/url")
    var request = URLRequest(url: endpoint)
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

    let body = IngestByUrlRequest(url: url)
    request.httpBody = try JSONEncoder.snakeCase.encode(body)

    let (data, _) = try await URLSession.shared.data(for: request)
    return try JSONDecoder.snakeCase.decode(IngestionYoutubeResponse.self, from: data)
}
```

---

## 6. iOS UI Guidance for Topic Opinions

### Recommended Screen Layout

```
+----------------------------------------------+
|  Dolar (USD/TRY)                    30 gun v |  <- topic header + days picker
+----------------------------------------------+
|                                              |
|  [Atilla Yesilada]         12 yorum  BEARISH |  <- channel group header
|  ------------------------------------------ |
|  10 Nis - Dolar kisa vadede 38 seviyesine    |  <- entry card
|  gerileyebilir...                            |
|  Seviyeler: 38.0 / 39.5          %92 guven   |
|  ------------------------------------------ |
|  03 Nis - Merkez Bankasi doviz               |  <- entry card
|  rezervlerini kullanarak...                  |
|  ------------------------------------------ |
|                                              |
|  [Taner Genek]              5 yorum  BULLISH |  <- channel group header
|  ------------------------------------------ |
|  09 Nis - Dolar guclenmeye devam ediyor,     |
|  40 seviyesi hedefte.                        |
|  Seviyeler: 39.0 / 40.0          %88 guven   |
|  ------------------------------------------ |
+----------------------------------------------+
```

### Sentiment Color Coding

| Sentiment | Color | Use |
|---|---|---|
| `bullish` | Green | Channel expects price/value to rise |
| `bearish` | Red | Channel expects price/value to fall |
| `neutral` | Gray | No clear directional view |
| `null` | Light gray | Sentiment not determined |

### Empty State

When `total_channels == 0`, show a message like:
> "Bu konu hakkinda henuz yorum bulunmuyor."
> ("No opinions found for this topic yet.")

---

## 7. Available Topic Slugs Reference

Use these as the `topic_slug` path parameter for the opinions endpoint:

**Economy subtopics** (`ekonomi`):
`borsa-istanbul`, `altin`, `gumus`, `dolar`, `bitcoin-kripto`, `dow-jones`, `petrol`, `faiz`, `enflasyon`

**Politics subtopics** (`siyaset`):
`ic-siyaset`, `dis-siyaset`, `jeopolitik`

**Main topics** (also work, but will have fewer targeted mentions):
`ekonomi`, `siyaset`, `spor`, `teknoloji`

Fetch dynamically: `GET /topics/tree`

---

## 8. Migration Checklist

- [ ] Add `TopicOpinionsResponse`, `ChannelOpinionGroup`, `ChannelOpinionEntry` Swift models
- [ ] Add `IngestByUrlRequest` Swift model
- [ ] Add `primaryTopicSlug`, `expectedSubtopics`, `legacyPersonId`, `updatedAt` to `ChannelResponse`
- [ ] Implement topic opinions fetch (`GET /topics/{slug}/opinions`)
- [ ] Implement URL ingestion (`POST /ingestions/youtube/url`)
- [ ] Add topic opinions screen/view
- [ ] Use `snake_case` JSON decoding strategy for all new models
- [ ] Handle 404 for unknown topic slugs
- [ ] Handle empty state when `total_channels == 0`

---

## 9. Backend Infrastructure Notes

- **Database**: Migrated from SQLite to PostgreSQL (no API changes)
- **YouTube fetcher**: Switched from oEmbed/HTML scraping to yt-dlp (more reliable metadata, no API changes)
- **LLM**: Gemini API with channel-aware prompting (existing classification responses unchanged)
- **All changes are backwards compatible**: no existing endpoints removed or modified in breaking ways
