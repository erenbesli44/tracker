# Channel-Aware Classification System

Status: Implemented
Date: 2026-04-12

Guide for iOS developers integrating the new channel-aware topic classification.

---

## 1. What Changed

Previously, classification was **context-free**: every video was classified against the full topic taxonomy with no awareness of what the channel typically covers. This caused:

- Irrelevant or low-confidence topics appearing for specialized channels
- No way to distinguish expected vs. unexpected classifications
- No channel-level topic focus metadata

**Now**, each channel can declare:

1. **`primary_topic_slug`** - What main topic area the channel covers (e.g. `"ekonomi"`)
2. **`expected_subtopics`** - Which specific subtopics this channel typically discusses (e.g. `["bist", "gold", "usd_try", "crypto"]`)

The LLM classification prompt uses these to **prefer** the expected subtopics and only produce new/unexpected classifications when confidence for all expected subtopics is very low.

## 2. Affected API Contracts

### 2.1 ChannelResponse (Updated)

Two new nullable fields added:

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
  "primary_topic_slug": "ekonomi",
  "expected_subtopics": ["bist", "gold", "usd_try", "crypto", "oil"],
  "legacy_person_id": 1,
  "created_at": "2026-04-11T14:00:00",
  "updated_at": "2026-04-12T10:30:00"
}
```

| Field | Type | Nullable | Description |
|---|---|---|---|
| `primary_topic_slug` | `string` | Yes | Main topic slug this channel covers. Matches a slug from `GET /topics/` (e.g. `"ekonomi"`, `"siyaset"`, `"teknoloji"`) |
| `expected_subtopics` | `string[]` | Yes | List of subtopic slugs this channel is expected to discuss. Used by the LLM to prefer these during classification |

**Backwards compatible**: both fields default to `null` for existing channels. Classification still works without them (falls back to full taxonomy, same as before).

### 2.2 ChannelCreate (Updated)

You can now set topic focus when creating a channel:

```json
{
  "name": "Atilla Yesilada",
  "platform": "youtube",
  "channel_handle": "@atillayesilada",
  "primary_topic_slug": "ekonomi",
  "expected_subtopics": ["bist", "gold", "usd_try", "inflation", "interest_rates"]
}
```

### 2.3 ChannelUpdate (Updated)

Set or change topic focus on an existing channel:

```json
{
  "primary_topic_slug": "ekonomi",
  "expected_subtopics": ["bist", "gold", "usd_try", "crypto", "oil"]
}
```

### 2.4 ClassificationResponse / TopicMentionResponse (Unchanged)

The classification response contract is **not changed**. The `confidence` field now carries more meaning since the LLM follows stricter confidence rules:

```json
{
  "video_id": 10,
  "total_mentions": 3,
  "mentions": [
    {
      "id": 99,
      "video_id": 10,
      "channel_id": 1,
      "person_id": 1,
      "topic_id": 5,
      "summary": "Borsa Istanbul: BIST 100 endeksinde baski suruyor...",
      "sentiment": "bearish",
      "key_levels": ["9800", "10200"],
      "start_time": "00:01:00",
      "end_time": "00:05:00",
      "confidence": 0.85
    }
  ]
}
```

## 3. Confidence Thresholds

The classification system now enforces strict confidence rules:

| Confidence Range | Meaning | When Included |
|---|---|---|
| >= 0.70 | **Strong** - Speaker clearly discusses with data/analysis | Always |
| 0.40 - 0.69 | **Moderate** - Mentioned with some substance | Always |
| 0.30 - 0.39 | **Weak** - Brief mention or passing reference | Only for expected subtopics |
| < 0.30 | Below threshold | Never included |

**iOS UI guidance**:
- Show topics with confidence >= 0.70 prominently (primary cards, bold labels)
- Show 0.40-0.69 as secondary topics (smaller cards, lighter weight)
- Consider hiding or dimming 0.30-0.39 entries behind a "show all" toggle

## 4. Available Subtopic Slugs

These are the valid subtopic slugs for `expected_subtopics`:

**Economy/Finance** (parent: `ekonomi`):

| Slug | Display Name | Description |
|---|---|---|
| `borsa-istanbul` | Borsa Istanbul | BIST, XU100, hisse senedi |
| `altin` | Altin | Ons altin, gram altin, gold |
| `gumus` | Gumus | Ons gumus, silver |
| `dolar` | Dolar | USD/TRY, doviz, kur |
| `bitcoin-kripto` | Bitcoin/Kripto | Bitcoin, kripto para |
| `dow-jones` | Dow Jones | Dow Jones, S&P 500, yurt disi borsalar |
| `petrol` | Petrol | Brent petrol, ham petrol |
| `faiz` | Faiz | Merkez Bankasi faizi, mevduat |
| `enflasyon` | Enflasyon | TUFE, UFE, fiyat artisi |

**Politics** (parent: `siyaset`):

| Slug | Display Name | Description |
|---|---|---|
| `ic-siyaset` | Ic Siyaset | Turkiye ic politikasi |
| `dis-siyaset` | Dis Siyaset | Uluslararasi iliskiler |
| `jeopolitik` | Jeopolitik | Savas, uluslararasi krizler |

**Main topics** (use as `primary_topic_slug`):

| Slug | Display Name |
|---|---|
| `ekonomi` | Ekonomi |
| `siyaset` | Siyaset |
| `spor` | Spor |
| `teknoloji` | Teknoloji |

You can always fetch the current list from `GET /topics/tree` for a hierarchical view.

## 5. How to Configure a Channel

### Step 1: Identify channel's focus

Determine what the channel primarily covers and which subtopics are expected.

### Step 2: Set via PATCH

```bash
curl -X PATCH http://127.0.0.1:8000/channels/1 \
  -H "Content-Type: application/json" \
  -d '{
    "primary_topic_slug": "ekonomi",
    "expected_subtopics": ["borsa-istanbul", "altin", "dolar", "bitcoin-kripto", "petrol"]
  }'
```

Response `200`:

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
  "primary_topic_slug": "ekonomi",
  "expected_subtopics": ["borsa-istanbul", "altin", "dolar", "bitcoin-kripto", "petrol"],
  "legacy_person_id": 1,
  "created_at": "2026-04-11T14:00:00",
  "updated_at": "2026-04-12T10:30:00"
}
```

### Step 3: Ingest videos

No changes to ingestion calls. The system automatically reads the channel's topic focus during ingestion and passes it to the LLM.

```bash
curl -X POST http://127.0.0.1:8000/ingestions/youtube/url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.youtube.com/watch?v=VIDEO_ID"}'
```

The classification results will now reflect the channel's expected subtopics.

## 6. Classification Behavior

### With channel topic focus configured

1. LLM sees the channel's `primary_topic_slug` and `expected_subtopics`
2. Prefers classifying into expected subtopics when evidence exists (confidence >= 0.30)
3. Only includes non-expected subtopics if they have high confidence (>= 0.70)
4. Only creates truly novel classifications when NO expected subtopic reaches 0.40
5. Maximum 7 topic segments per video, sorted by confidence

### Without channel topic focus (null fields)

Falls back to the full taxonomy with no preference — same behavior as before this update.

### Fallback (keyword-based, when LLM unavailable)

If the LLM call fails, the existing keyword-based auto-classification takes over. This fallback does not use channel topic focus — it classifies based on keyword matching against the transcript.

## 7. iOS Swift Model Updates

### Updated ChannelResponse model

```swift
struct ChannelResponse: Codable, Identifiable {
    let id: Int
    let name: String
    let slug: String
    let platform: String
    let channelHandle: String?
    let youtubeChannelId: String?
    let channelUrl: String?
    let bio: String?
    let primaryTopicSlug: String?       // NEW
    let expectedSubtopics: [String]?    // NEW
    let legacyPersonId: Int?
    let createdAt: String
    let updatedAt: String?
}
```

### Channel update request model

```swift
struct ChannelUpdateRequest: Codable {
    var name: String?
    var platform: String?
    var channelHandle: String?
    var youtubeChannelId: String?
    var channelUrl: String?
    var bio: String?
    var primaryTopicSlug: String?       // NEW
    var expectedSubtopics: [String]?    // NEW
}
```

### Example: Update channel topic focus

```swift
func updateChannelTopicFocus(
    channelId: Int,
    primaryTopic: String,
    subtopics: [String]
) async throws -> ChannelResponse {
    let url = baseURL.appendingPathComponent("channels/\(channelId)")
    var request = URLRequest(url: url)
    request.httpMethod = "PATCH"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")

    let body = ChannelUpdateRequest(
        primaryTopicSlug: primaryTopic,
        expectedSubtopics: subtopics
    )
    request.httpBody = try JSONEncoder.snakeCase.encode(body)

    let (data, _) = try await URLSession.shared.data(for: request)
    return try JSONDecoder.snakeCase.decode(ChannelResponse.self, from: data)
}
```

### Example: Confidence-based UI rendering

```swift
struct TopicMentionRow: View {
    let mention: TopicMentionResponse

    var confidenceLevel: ConfidenceLevel {
        switch mention.confidence {
        case 0.70...:  return .strong
        case 0.40..<0.70: return .moderate
        case 0.30..<0.40: return .weak
        default: return .none
        }
    }

    var body: some View {
        HStack {
            // Sentiment indicator
            Circle()
                .fill(sentimentColor)
                .frame(width: 8, height: 8)

            VStack(alignment: .leading) {
                Text(mention.summary)
                    .font(confidenceLevel == .strong ? .body.bold() : .body)
                    .foregroundColor(confidenceLevel == .weak ? .secondary : .primary)

                if let levels = mention.keyLevels, !levels.isEmpty {
                    Text(levels.joined(separator: " / "))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            // Confidence badge
            Text("\(Int(mention.confidence * 100))%")
                .font(.caption2)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(confidenceBadgeColor)
                .cornerRadius(4)
        }
    }

    private var sentimentColor: Color {
        switch mention.sentiment {
        case "bullish": return .green
        case "bearish": return .red
        default: return .gray
        }
    }

    private var confidenceBadgeColor: Color {
        switch confidenceLevel {
        case .strong: return .green.opacity(0.2)
        case .moderate: return .orange.opacity(0.2)
        case .weak: return .gray.opacity(0.2)
        case .none: return .clear
        }
    }
}

enum ConfidenceLevel {
    case strong, moderate, weak, none
}
```

## 8. Data Flow Diagram

```
iOS App                          Backend API                        LLM (Gemini)
  |                                  |                                  |
  |-- PATCH /channels/{id} -------->|                                  |
  |   (set primary_topic_slug,      |                                  |
  |    expected_subtopics)          |                                  |
  |<-------- 200 ChannelResponse ---|                                  |
  |                                  |                                  |
  |-- POST /ingestions/youtube/url ->|                                  |
  |                                  |-- fetch transcript              |
  |                                  |-- read channel topic focus      |
  |                                  |-- build prompt with channel     |
  |                                  |   context + expected subtopics  |
  |                                  |-- call LLM ------------------->|
  |                                  |                                  |
  |                                  |<-- JSON with topic_segments,    |
  |                                  |    is_expected, confidence ------|
  |                                  |                                  |
  |                                  |-- map subtopics to DB topics    |
  |                                  |-- store TopicMentions           |
  |<-------- 200 IngestionResponse --|                                  |
  |                                  |                                  |
  |-- GET /videos/{id}/classification|                                  |
  |<-------- 200 ClassificationResp -|                                  |
  |   (mentions now reflect          |                                  |
  |    channel's expected topics)    |                                  |
```

## 9. Migration Notes

- **No breaking changes**: All existing endpoints and response shapes are preserved.
- **New fields are nullable**: `primary_topic_slug` and `expected_subtopics` default to `null`.
- **Gradual rollout**: Configure channels one by one via PATCH. Unconfigured channels work exactly as before.
- **DB migration is automatic**: The SQLite migration runs on app startup (adds the two columns if missing).
- **iOS model update is additive**: Just add the two optional fields to your `ChannelResponse` Swift struct. Existing decoding will not break since both are nullable.

## 10. Files Changed (Backend Reference)

| File | Change |
|---|---|
| `src/channels/models.py` | Added `primary_topic_slug`, `expected_subtopics` fields |
| `src/channels/schemas.py` | Added fields to `ChannelCreate`, `ChannelUpdate`, `ChannelResponse` + JSON validator |
| `src/channels/service.py` | Added JSON encode/decode helpers, wired new fields into `create()` / `update()` |
| `src/database.py` | Added SQLite migration for the two new columns |
| `src/llm/prompts.py` | Rewrote `ANALYSIS_PROMPT_TEMPLATE` with channel context and confidence rules |
| `src/llm/service.py` | Extended `_build_prompt()` and `generate_analysis_json()` with channel context params |
| `src/ingestion/service.py` | Passes channel topic context to LLM, handles `other:*` novel topics, added new subtopic mappings |
