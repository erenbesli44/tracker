import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

os.environ["API_KEY"] = "test-api-key"
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["YOUTUBE_PROXY_ENABLED"] = "false"
os.environ["WEBSHARE_API_KEY"] = ""

from src.config import settings
from src.database import get_session
from src.main import app


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # import all models so their metadata is registered
    import src.channels.models  # noqa: F401
    import src.classification.models  # noqa: F401
    import src.persons.models  # noqa: F401
    import src.topics.models  # noqa: F401
    import src.videos.models  # noqa: F401

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        # Seed topics for tests
        from src.topics import service as topics_service
        from src.topics.constants import TOPIC_SEED

        topics_service.seed_topics(session, TOPIC_SEED)
        yield session


@pytest.fixture(name="client")
async def client_fixture(session: Session):
    def get_session_override():
        yield session

    app.dependency_overrides[get_session] = get_session_override
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": settings.API_KEY},
    ) as client:
        yield client
    app.dependency_overrides.clear()
