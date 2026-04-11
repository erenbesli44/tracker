# Database Architecture — Social Media Tracker

## Design Principles

- **Minimalist**: fewest tables that still support all required features
- **Self-referential topics**: single `topic` table with `parent_id` for main/sub hierarchy
- **Timeline-first**: every data point links to a source + timestamp for chronological tracking
- **Phase 1**: SQLite with SQLModel ORM (easy migration to PostgreSQL later)

---

## Entity Relationship

```
person 1──N video 1──1 transcript
                │
                └──N topic_mention N──1 topic (self-ref: parent_id)
```

---

## Tables

### `person`
Tracked influencer/content creator.

| Column           | Type        | Constraints          | Notes                          |
|------------------|-------------|----------------------|--------------------------------|
| id               | INTEGER     | PK, autoincrement    |                                |
| name             | TEXT        | NOT NULL             | Display name                   |
| slug             | TEXT        | NOT NULL, UNIQUE     | URL-safe identifier            |
| platform         | TEXT        | NOT NULL, DEFAULT 'youtube' | Primary platform       |
| platform_handle  | TEXT        | NULL                 | e.g., YouTube channel ID       |
| bio              | TEXT        | NULL                 | Short description              |
| created_at       | DATETIME    | NOT NULL, DEFAULT NOW|                                |
| updated_at       | DATETIME    | NULL                 |                                |

### `video`
A single YouTube video (or other source in future).

| Column       | Type        | Constraints          | Notes                             |
|--------------|-------------|----------------------|-----------------------------------|
| id           | INTEGER     | PK, autoincrement    |                                   |
| person_id    | INTEGER     | FK → person.id, NOT NULL |                               |
| platform     | TEXT        | NOT NULL, DEFAULT 'youtube' |                            |
| video_url    | TEXT        | NOT NULL, UNIQUE     | Full YouTube URL                  |
| video_id     | TEXT        | NOT NULL             | YouTube video ID (e.g., bnk8WqphWMw) |
| title        | TEXT        | NULL                 |                                   |
| published_at | DATETIME    | NULL                 | When the video was published      |
| duration     | INTEGER     | NULL                 | Duration in seconds               |
| created_at   | DATETIME    | NOT NULL, DEFAULT NOW|                                   |

### `transcript`
Full transcript text for a video. One-to-one with video.

| Column       | Type        | Constraints          | Notes                          |
|--------------|-------------|----------------------|--------------------------------|
| id           | INTEGER     | PK, autoincrement    |                                |
| video_id     | INTEGER     | FK → video.id, UNIQUE, NOT NULL | One transcript per video |
| raw_text     | TEXT        | NOT NULL             | Full transcript text           |
| language     | TEXT        | DEFAULT 'tr'         | Detected language              |
| created_at   | DATETIME    | NOT NULL, DEFAULT NOW|                                |

### `topic`
Hierarchical topic taxonomy. `parent_id = NULL` → main topic; otherwise sub-topic.

| Column       | Type        | Constraints          | Notes                          |
|--------------|-------------|----------------------|--------------------------------|
| id           | INTEGER     | PK, autoincrement    |                                |
| name         | TEXT        | NOT NULL             | Display name (e.g., "Ekonomi") |
| slug         | TEXT        | NOT NULL, UNIQUE     | URL-safe (e.g., "ekonomi")     |
| parent_id    | INTEGER     | FK → topic.id, NULL  | NULL = main topic              |
| description  | TEXT        | NULL                 | What this topic covers         |
| created_at   | DATETIME    | NOT NULL, DEFAULT NOW|                                |

**Seed data example:**

```
Main Topics:
├── Ekonomi (economy)
│   ├── Borsa İstanbul (BIST)
│   ├── Dolar (USD)
│   ├── Altın (Gold)
│   ├── Gümüş (Silver)
│   ├── Dow Jones
│   ├── Petrol (Oil/Brent)
│   ├── Bitcoin / Kripto
│   ├── Faiz (Interest rates)
│   └── Enflasyon (Inflation)
├── Siyaset (Politics)
│   ├── İç Siyaset (Domestic)
│   ├── Dış Siyaset (Foreign)
│   └── Jeopolitik (Geopolitics)
├── Spor (Sports)
└── Teknoloji (Technology)
```

### `topic_mention`
Core tracking table: what did a person say about a topic, in which video, at what time.

| Column       | Type        | Constraints          | Notes                                |
|--------------|-------------|----------------------|--------------------------------------|
| id           | INTEGER     | PK, autoincrement    |                                      |
| video_id     | INTEGER     | FK → video.id, NOT NULL |                                   |
| person_id    | INTEGER     | FK → person.id, NOT NULL | Denormalized for fast timeline queries |
| topic_id     | INTEGER     | FK → topic.id, NOT NULL |                                   |
| summary      | TEXT        | NOT NULL             | What they said about this topic      |
| sentiment    | TEXT        | NULL                 | 'bullish', 'bearish', 'neutral'      |
| key_levels   | TEXT        | NULL                 | JSON: key price levels mentioned     |
| start_time   | TEXT        | NULL                 | Timestamp in video (e.g., "2:51")    |
| end_time     | TEXT        | NULL                 | End timestamp (e.g., "6:47")         |
| confidence   | REAL        | DEFAULT 1.0          | Classification confidence (0-1)      |
| created_at   | DATETIME    | NOT NULL, DEFAULT NOW|                                      |

**Composite index**: `(video_id, topic_id)` — fast lookup per video per topic.
**Index**: `(person_id, topic_id, created_at)` — **primary timeline index** (direct query, no joins).
**Index**: `(topic_id, created_at)` — cross-person timeline queries.

---

## Key Queries This Architecture Supports

### 1. Timeline: What did Taner Genek say about Borsa over time?
```sql
-- Uses (person_id, topic_id, created_at) index — single index scan, 1 join only
SELECT tm.summary, tm.sentiment, tm.key_levels, tm.start_time, tm.end_time,
       v.title, v.published_at, v.video_url
FROM topic_mention tm
JOIN video v ON tm.video_id = v.id
WHERE tm.person_id = 1
  AND tm.topic_id = (SELECT id FROM topic WHERE slug = 'borsa-istanbul')
ORDER BY v.published_at DESC;
```

### 2. Latest opinion on a sub-topic
```sql
SELECT tm.summary, tm.sentiment, tm.key_levels,
       v.title, v.published_at, v.video_url
FROM topic_mention tm
JOIN video v ON tm.video_id = v.id
WHERE tm.person_id = 1
  AND tm.topic_id = (SELECT id FROM topic WHERE slug = 'altin')
ORDER BY v.published_at DESC
LIMIT 1;
```

### 3. All topics a person discussed in a single video
```sql
SELECT t.name, tp.name AS parent_topic, tm.summary, tm.sentiment
FROM topic_mention tm
JOIN topic t ON tm.topic_id = t.id
LEFT JOIN topic tp ON t.parent_id = tp.id
WHERE tm.video_id = ?
ORDER BY tm.start_time;
```

### 4. Compare what they said across videos
```sql
SELECT v.title, v.published_at, tm.summary, tm.sentiment
FROM topic_mention tm
JOIN video v ON tm.video_id = v.id
WHERE tm.topic_id = ? AND v.person_id = ?
ORDER BY v.published_at;
```

---

## SQLite-Specific Notes

- Use `TEXT` for datetime columns, store as ISO 8601 strings
- JSON fields stored as `TEXT` (SQLite supports `json_extract()`)
- For Phase 2 migration to PostgreSQL: swap TEXT datetimes for TIMESTAMP, TEXT JSON for JSONB
- SQLModel handles this abstraction cleanly
