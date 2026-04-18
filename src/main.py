import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
from src.database import create_db_and_tables, run_lightweight_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Import all table models so they are registered in SQLModel metadata
    import src.persons.models  # noqa: F401
    import src.channels.models  # noqa: F401
    import src.videos.models  # noqa: F401
    import src.topics.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.jobs.youtube_watch.models  # noqa: F401

    create_db_and_tables()
    run_lightweight_migrations()

    # Seed default topic taxonomy
    from sqlmodel import Session
    from src.database import engine
    from src.topics import service as topics_service
    from src.topics.constants import TOPIC_SEED

    with Session(engine) as session:
        topics_service.seed_topics(session, TOPIC_SEED)

    yield


app_configs: dict = {
    "title": "Social Media Tracker API",
    "description": "Track content creators' YouTube shares — classify by topic and query timelines.",
    "version": settings.APP_VERSION,
    "lifespan": lifespan,
}

if settings.ENVIRONMENT not in ("local", "development"):
    app_configs["openapi_url"] = None

app = FastAPI(**app_configs)

# ── Routers ────────────────────────────────────────────────────────────────
from src.persons.router import router as persons_router  # noqa: E402
from src.topics.router import router as topics_router  # noqa: E402
from src.channels.router import router as channels_router  # noqa: E402
from src.videos.router import router as videos_router  # noqa: E402
from src.classification.router import router as classification_router  # noqa: E402
from src.timeline.router import router as timeline_router  # noqa: E402
from src.ingestion.router import router as ingestion_router  # noqa: E402
from src.jobs.youtube_watch.router import router as youtube_watch_router  # noqa: E402

app.include_router(persons_router)
app.include_router(topics_router)
app.include_router(channels_router)
app.include_router(videos_router)
app.include_router(classification_router)
app.include_router(timeline_router)
app.include_router(ingestion_router)
app.include_router(youtube_watch_router)


@app.get("/health", tags=["health"])
def health_check() -> dict:
    return {"status": "ok", "version": settings.APP_VERSION}
