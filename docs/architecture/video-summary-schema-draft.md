# Schema Draft - `video_summary`

Status: Draft  
Date: 2026-04-11  
Purpose: Phase 1B summary persistence schema and endpoint contract support.

## 1. Problem

Current data model stores transcript and topic mentions, but summary is not persisted as a first-class entity.

Need:

1. one canonical summary record per video
2. support manual and future LLM-generated summaries
3. simple read/write API for frontend testing

## 2. Proposed Table

Table name: `video_summary`  
Cardinality: 1:1 with `video`

### SQL DDL (SQLite/PostgreSQL-compatible intent)

```sql
CREATE TABLE video_summary (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER NOT NULL UNIQUE,
  short_summary TEXT NOT NULL,
  long_summary TEXT NULL,
  highlights TEXT NULL,                  -- JSON string array: ["...", "..."]
  language VARCHAR(10) NOT NULL DEFAULT 'tr',
  source VARCHAR(20) NOT NULL DEFAULT 'manual', -- manual | llm
  created_at DATETIME NOT NULL,
  updated_at DATETIME NULL,
  FOREIGN KEY(video_id) REFERENCES video(id)
);

CREATE UNIQUE INDEX ix_video_summary_video_id ON video_summary (video_id);
CREATE INDEX ix_video_summary_source ON video_summary (source);
```

## 3. SQLModel Draft

```python
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

from src.models import utc_now


class VideoSummary(SQLModel, table=True):
    __tablename__ = "video_summary"

    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: int = Field(foreign_key="video.id", unique=True, index=True)
    short_summary: str
    long_summary: Optional[str] = None
    highlights: Optional[str] = None  # JSON string list
    language: str = Field(default="tr", max_length=10)
    source: str = Field(default="manual", max_length=20)  # manual | llm
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: Optional[datetime] = None
```

## 4. API Contract Alignment

Planned endpoints:

1. `POST /videos/{video_id}/summary`
2. `GET /videos/{video_id}/summary`

### Request schema (`POST`)

```json
{
  "short_summary": "Kisa ozet",
  "long_summary": "Detayli ozet",
  "highlights": ["Madde 1", "Madde 2"],
  "language": "tr",
  "source": "manual"
}
```

### Response schema (`GET` and `POST`)

```json
{
  "id": 10,
  "video_id": 25,
  "short_summary": "Kisa ozet",
  "long_summary": "Detayli ozet",
  "highlights": ["Madde 1", "Madde 2"],
  "language": "tr",
  "source": "manual",
  "created_at": "2026-04-11T10:30:00",
  "updated_at": "2026-04-11T10:45:00"
}
```

## 5. Storage and Update Rules

1. `video_id` uniqueness enforces one active summary per video.
2. `POST /videos/{video_id}/summary` behaves as upsert:
1. create if no record
2. replace fields if exists
3. set `updated_at` on update
3. `highlights` persisted as JSON string in DB and returned as `list[str]`.
4. `source` is `manual` in Phase 1 unless endpoint explicitly marked LLM output.

## 6. Validation Rules

1. `short_summary` required and non-empty.
2. `language` max length 10.
3. `source` enum: `manual | llm`.
4. `video_id` must exist, else `404`.

## 7. Migration Strategy

Because current app uses `SQLModel.metadata.create_all` on startup:

1. add new model file/class (register in app startup imports)
2. deploy startup once to create table in SQLite
3. verify with:

```sql
.tables
.schema video_summary
```

For production maturity, adopt Alembic migration in later phase.

## 8. Query Examples

### Read summary by video

```sql
SELECT *
FROM video_summary
WHERE video_id = ?;
```

### Join summary with video and person

```sql
SELECT p.name, v.title, v.video_url, s.short_summary, s.source, s.updated_at
FROM video_summary s
JOIN video v ON s.video_id = v.id
JOIN person p ON v.person_id = p.id
WHERE v.id = ?;
```

