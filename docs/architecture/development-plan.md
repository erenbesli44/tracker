# Development Plan — Social Media Tracker (Phase 1)

## Project Summary

Track social media influencers' YouTube video content: classify transcripts into main topics (ekonomi, siyaset, spor) and sub-topics (borsa, altın, gümüş, bitcoin, etc.), and provide timeline queries to see what a person said about a topic across videos.

**Phase 1 scope**: FastAPI + SQLite + manual transcript upload + prompt-based classification (no LLM API yet).

---

## Tech Stack

| Component        | Choice          | Why                                          |
|------------------|-----------------|----------------------------------------------|
| Framework        | FastAPI         | Async-ready, Pydantic integration, OpenAPI   |
| ORM              | SQLModel        | Pydantic + SQLAlchemy hybrid, recommended    |
| Database         | SQLite          | Zero-config, file-based, sufficient for MVP  |
| Package Manager  | uv              | Fast, modern Python package manager          |
| Linting          | Ruff            | Fast, replaces black + isort + flake8        |
| Testing          | pytest + httpx  | Async test client from day 0                 |
| Python           | 3.12+           | Latest stable, modern syntax                 |

---

## Development Steps

### Step 1: Project Scaffolding
- [ ] Initialize project with `uv init`
- [ ] Set up `pyproject.toml` with dependencies:
  - `fastapi`
  - `sqlmodel`
  - `uvicorn`
  - `python-slugify`
- [ ] Dev dependencies: `pytest`, `httpx`, `pytest-asyncio`, `ruff`
- [ ] Create directory structure per [fastapi-checklist.md](fastapi-checklist.md)
- [ ] Configure Ruff in `pyproject.toml`
- [ ] Add `.env` with `DATABASE_URL=sqlite:///./tracker.db`
- [ ] Add `.gitignore`
- [ ] Set FastAPI entrypoint in `pyproject.toml`

### Step 2: Database Layer
- [ ] `src/database.py` — SQLModel engine, session dependency with `yield`
- [ ] `src/models.py` — CustomModel base with `from_attributes=True`
- [ ] Define all SQLModel models:
  - `src/persons/models.py` → `Person`
  - `src/videos/models.py` → `Video`, `Transcript`
  - `src/topics/models.py` → `Topic`
  - `src/classification/models.py` → `TopicMention`
- [ ] Create tables on startup via `SQLModel.metadata.create_all()`
- [ ] Add seed data script for topics taxonomy (ekonomi, siyaset, etc.)

### Step 3: Persons Module
- [ ] `src/persons/schemas.py`:
  - `PersonCreate` — name, platform, platform_handle, bio
  - `PersonResponse` — all fields + id, created_at
  - `PersonUpdate` — optional fields
- [ ] `src/persons/service.py`:
  - `create(data)`, `get_by_id(id)`, `get_by_slug(slug)`, `list_all()`, `update(id, data)`
- [ ] `src/persons/dependencies.py`:
  - `valid_person_id(person_id) -> Person` — raises PersonNotFound
- [ ] `src/persons/router.py`:
  - `POST /persons/` → create person
  - `GET /persons/` → list persons
  - `GET /persons/{person_id}` → get person
  - `PATCH /persons/{person_id}` → update person
- [ ] `src/persons/exceptions.py` → `PersonNotFound`
- [ ] Tests: `tests/persons/test_router.py`

### Step 4: Topics Module
- [ ] `src/topics/schemas.py`:
  - `TopicCreate` — name, parent_id (optional), description
  - `TopicResponse` — all fields + sub_topics list
  - `TopicTreeResponse` — nested tree structure
- [ ] `src/topics/service.py`:
  - `create(data)`, `get_by_id(id)`, `get_by_slug(slug)`, `list_main_topics()`, `get_sub_topics(parent_id)`, `get_topic_tree()`
- [ ] `src/topics/dependencies.py`:
  - `valid_topic_id(topic_id) -> Topic` — raises TopicNotFound
- [ ] `src/topics/router.py`:
  - `POST /topics/` → create topic
  - `GET /topics/` → list all (flat)
  - `GET /topics/tree` → hierarchical tree
  - `GET /topics/{topic_id}` → get topic with sub-topics
- [ ] `src/topics/exceptions.py` → `TopicNotFound`
- [ ] Seed script: populate initial topic taxonomy
- [ ] Tests: `tests/topics/test_router.py`

### Step 5: Videos Module
- [ ] `src/videos/schemas.py`:
  - `VideoCreate` — person_id, video_url, title, published_at
  - `VideoResponse` — all fields
  - `VideoWithTranscript` — video + transcript
  - `TranscriptCreate` — raw_text, language
- [ ] `src/videos/service.py`:
  - `create(data)` — extract video_id from URL
  - `get_by_id(id)`, `list_by_person(person_id)`, `add_transcript(video_id, data)`
- [ ] `src/videos/dependencies.py`:
  - `valid_video_id(video_id) -> Video` — raises VideoNotFound
- [ ] `src/videos/router.py`:
  - `POST /videos/` → create video
  - `GET /videos/` → list videos (filter by person_id)
  - `GET /videos/{video_id}` → get video with transcript
  - `POST /videos/{video_id}/transcript` → upload transcript
- [ ] `src/videos/exceptions.py` → `VideoNotFound`, `TranscriptAlreadyExists`
- [ ] Tests: `tests/videos/test_router.py`

### Step 6: Classification Module
- [ ] `src/classification/schemas.py`:
  - `TopicMentionCreate` — topic_id, summary, sentiment, key_levels, start_time, end_time, confidence
  - `TopicMentionResponse` — all fields + topic info
  - `ClassificationRequest` — video_id, topic_mentions list
  - `ClassificationResponse` — created mentions
- [ ] `src/classification/service.py`:
  - `classify_video(video_id, mentions)` — store topic mentions
  - `get_mentions_by_video(video_id)` — all mentions for a video
  - `get_classification_prompt()` — return prompt template for reference
- [ ] `src/classification/constants.py`:
  - Store the classification system prompt and user prompt templates
  - Store topic taxonomy keywords for future keyword-based pre-classification
- [ ] `src/classification/router.py`:
  - `POST /videos/{video_id}/classify` → submit classification results
  - `GET /videos/{video_id}/classification` → get video's classification
  - `GET /classification/prompt` → return the prompt template (for dev reference)
- [ ] Tests: `tests/classification/test_router.py`

### Step 7: Timeline Module
- [ ] `src/timeline/schemas.py`:
  - `TimelineEntry` — video title, published_at, summary, sentiment, key_levels, video_url, start_time
  - `TimelineResponse` — list of entries + topic info
  - `PersonTopicSummary` — topic name, mention_count, latest_sentiment, latest_summary
- [ ] `src/timeline/service.py`:
  - `get_topic_timeline(person_id, topic_id)` — chronological mentions
  - `get_person_topics_overview(person_id)` — all topics with latest mentions
  - `get_latest_mention(person_id, topic_id)` — most recent opinion
- [ ] `src/timeline/router.py`:
  - `GET /persons/{person_id}/timeline` → all topic mentions for a person (optionally filtered by topic)
  - `GET /persons/{person_id}/topics/{topic_id}/timeline` → single topic timeline
  - `GET /persons/{person_id}/topics/overview` → summary of all topics
- [ ] Tests: `tests/timeline/test_router.py`

### Step 8: App Assembly
- [ ] `src/main.py`:
  - Init FastAPI app with title, description, version
  - Include all routers
  - Add startup event to create tables + seed topics
  - Configure CORS if needed
- [ ] `src/config.py`:
  - `Settings(BaseSettings)` — DATABASE_URL, ENVIRONMENT, APP_VERSION
- [ ] `src/exceptions.py`:
  - Global exception handlers for 404, validation errors

### Step 9: Development Data Loading
- [ ] Create `scripts/load_dev_data.py`:
  - Create "Taner Genek" person
  - Create 2 videos from dummy examples
  - Load transcripts from `dummy/taner_genek/example-1` and `example-2`
  - Submit pre-classified topic mentions (from classification-prompt.md examples)
- [ ] Verify timeline queries work with dev data

### Step 10: Testing & Polish
- [ ] Run full test suite
- [ ] Ruff lint + format
- [ ] Verify all endpoints in Swagger UI (`/docs`)
- [ ] Verify timeline queries return correct chronological data
- [ ] Update README

---

## API Endpoint Summary

| Method | Path                                          | Description                          |
|--------|-----------------------------------------------|--------------------------------------|
| POST   | `/persons/`                                   | Create tracked person                |
| GET    | `/persons/`                                   | List all persons                     |
| GET    | `/persons/{person_id}`                        | Get person details                   |
| PATCH  | `/persons/{person_id}`                        | Update person                        |
| POST   | `/topics/`                                    | Create topic                         |
| GET    | `/topics/`                                    | List all topics                      |
| GET    | `/topics/tree`                                | Get topic hierarchy                  |
| GET    | `/topics/{topic_id}`                          | Get topic + sub-topics               |
| POST   | `/videos/`                                    | Create video record                  |
| GET    | `/videos/`                                    | List videos (filter by person)       |
| GET    | `/videos/{video_id}`                          | Get video + transcript               |
| POST   | `/videos/{video_id}/transcript`               | Upload transcript                    |
| POST   | `/videos/{video_id}/classify`                 | Submit classification                |
| GET    | `/videos/{video_id}/classification`           | Get video classification             |
| GET    | `/classification/prompt`                      | Get prompt template                  |
| GET    | `/persons/{person_id}/timeline`               | All mentions for person              |
| GET    | `/persons/{person_id}/topics/{topic_id}/timeline` | Topic-specific timeline          |
| GET    | `/persons/{person_id}/topics/overview`        | Topic summary for person             |

---

## Phase 2 Roadmap (Not In Scope)

1. **YouTube API Integration**: Auto-fetch video metadata + transcript via `youtube-transcript-api`
2. **LLM Classification**: Send transcript + prompt to OpenAI/Anthropic API, parse JSON response
3. **PostgreSQL Migration**: Swap SQLite → PostgreSQL, add Alembic migrations
4. **Background Tasks**: Use FastAPI BackgroundTasks or Celery for async classification
5. **Multi-Platform**: Support Twitter/X, podcasts, etc.
6. **Search**: Full-text search across summaries
7. **Auth**: API key or OAuth for protected endpoints
8. **Frontend**: Dashboard to visualize timelines

---

## Files Reference

| File | Purpose |
|------|---------|
| [db-design.md](db-design.md) | Database schema, tables, indexes, example queries |
| [classification-prompt.md](classification-prompt.md) | LLM prompt template + example classification output |
| [fastapi-checklist.md](fastapi-checklist.md) | FastAPI coding rules extracted from best practices |
