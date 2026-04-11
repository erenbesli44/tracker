from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from src.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_db_and_tables() -> None:
    # All models must be imported before this call so their metadata is registered.
    # Imports are done in src/main.py before startup.
    SQLModel.metadata.create_all(engine)


def _sqlite_has_column(session: Session, table_name: str, column_name: str) -> bool:
    rows = session.exec(text(f"PRAGMA table_info({table_name})")).all()
    return any(row[1] == column_name for row in rows)


def _sqlite_is_not_null_column(session: Session, table_name: str, column_name: str) -> bool:
    rows = session.exec(text(f"PRAGMA table_info({table_name})")).all()
    for row in rows:
        # PRAGMA table_info columns:
        # 0: cid, 1: name, 2: type, 3: notnull, 4: dflt_value, 5: pk
        if row[1] == column_name:
            return bool(row[3])
    return False


def _rebuild_video_table_nullable_person(session: Session) -> None:
    session.exec(
        text(
            """
            CREATE TABLE video_new (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                person_id INTEGER,
                platform VARCHAR(50) NOT NULL,
                video_url VARCHAR(500) NOT NULL UNIQUE,
                video_id VARCHAR(100) NOT NULL,
                title VARCHAR(500),
                published_at DATETIME,
                duration INTEGER,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(channel_id) REFERENCES youtube_channel(id),
                FOREIGN KEY(person_id) REFERENCES person(id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT INTO video_new (
                id, channel_id, person_id, platform, video_url,
                video_id, title, published_at, duration, created_at
            )
            SELECT
                id, channel_id, person_id, platform, video_url,
                video_id, title, published_at, duration, created_at
            FROM video
            """
        )
    )
    session.exec(text("DROP TABLE video"))
    session.exec(text("ALTER TABLE video_new RENAME TO video"))


def _rebuild_topic_mention_table_nullable_person(session: Session) -> None:
    session.exec(
        text(
            """
            CREATE TABLE topic_mention_new (
                id INTEGER PRIMARY KEY,
                video_id INTEGER NOT NULL,
                channel_id INTEGER,
                person_id INTEGER,
                topic_id INTEGER NOT NULL,
                summary VARCHAR NOT NULL,
                sentiment VARCHAR(20),
                key_levels VARCHAR,
                start_time VARCHAR(20),
                end_time VARCHAR(20),
                confidence FLOAT NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(video_id) REFERENCES video(id),
                FOREIGN KEY(channel_id) REFERENCES youtube_channel(id),
                FOREIGN KEY(person_id) REFERENCES person(id),
                FOREIGN KEY(topic_id) REFERENCES topic(id)
            )
            """
        )
    )
    session.exec(
        text(
            """
            INSERT INTO topic_mention_new (
                id, video_id, channel_id, person_id, topic_id, summary,
                sentiment, key_levels, start_time, end_time, confidence, created_at
            )
            SELECT
                id, video_id, channel_id, person_id, topic_id, summary,
                sentiment, key_levels, start_time, end_time, confidence, created_at
            FROM topic_mention
            """
        )
    )
    session.exec(text("DROP TABLE topic_mention"))
    session.exec(text("ALTER TABLE topic_mention_new RENAME TO topic_mention"))


def _ensure_post_rebuild_indexes(session: Session) -> None:
    # video indexes
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_video_person_id ON video(person_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_video_video_id ON video(video_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_video_channel_id ON video(channel_id)"))

    # topic_mention single-column indexes
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_topic_mention_video_id ON topic_mention(video_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_topic_mention_person_id ON topic_mention(person_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_topic_mention_topic_id ON topic_mention(topic_id)"))
    session.exec(text("CREATE INDEX IF NOT EXISTS ix_topic_mention_channel_id ON topic_mention(channel_id)"))

    # timeline composite indexes
    session.exec(
        text(
            "CREATE INDEX IF NOT EXISTS ix_topic_mention_person_topic_created "
            "ON topic_mention(person_id, topic_id, created_at)"
        )
    )
    session.exec(
        text(
            "CREATE INDEX IF NOT EXISTS ix_topic_mention_channel_topic_created "
            "ON topic_mention(channel_id, topic_id, created_at)"
        )
    )
    session.exec(
        text(
            "CREATE INDEX IF NOT EXISTS ix_topic_mention_video_topic "
            "ON topic_mention(video_id, topic_id)"
        )
    )


def run_lightweight_migrations() -> None:
    """Best-effort schema upgrades for SQLite environments without Alembic."""
    if engine.dialect.name != "sqlite":
        return

    with Session(engine) as session:
        if not _sqlite_has_column(session, "video", "channel_id"):
            session.exec(text("ALTER TABLE video ADD COLUMN channel_id INTEGER"))

        if not _sqlite_has_column(session, "topic_mention", "channel_id"):
            session.exec(text("ALTER TABLE topic_mention ADD COLUMN channel_id INTEGER"))

        # Finalize migration: make legacy person references optional.
        video_person_not_null = _sqlite_is_not_null_column(session, "video", "person_id")
        mention_person_not_null = _sqlite_is_not_null_column(session, "topic_mention", "person_id")
        if video_person_not_null or mention_person_not_null:
            session.exec(text("PRAGMA foreign_keys=OFF"))
            if video_person_not_null:
                _rebuild_video_table_nullable_person(session)
            if mention_person_not_null:
                _rebuild_topic_mention_table_nullable_person(session)
            session.exec(text("PRAGMA foreign_keys=ON"))

        _ensure_post_rebuild_indexes(session)

        # Backfill channels from legacy person rows.
        session.exec(
            text(
                """
                INSERT INTO youtube_channel (
                    name, slug, platform, channel_handle, bio,
                    legacy_person_id, created_at, updated_at
                )
                SELECT
                    p.name, p.slug, p.platform, p.platform_handle, p.bio,
                    p.id, p.created_at, p.updated_at
                FROM person p
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM youtube_channel c
                    WHERE c.legacy_person_id = p.id
                )
                """
            )
        )

        # Backfill video.channel_id from linked person.
        session.exec(
            text(
                """
                UPDATE video
                SET channel_id = (
                    SELECT c.id
                    FROM youtube_channel c
                    WHERE c.legacy_person_id = video.person_id
                    LIMIT 1
                )
                WHERE channel_id IS NULL
                """
            )
        )

        # Backfill topic_mention.channel_id from linked person.
        session.exec(
            text(
                """
                UPDATE topic_mention
                SET channel_id = (
                    SELECT c.id
                    FROM youtube_channel c
                    WHERE c.legacy_person_id = topic_mention.person_id
                    LIMIT 1
                )
                WHERE channel_id IS NULL
                """
            )
        )
        session.commit()
