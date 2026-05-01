import json
from typing import Annotated

from fastapi import Depends
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from src.config import settings

_engine_kwargs: dict = {"echo": False}
if settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)


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
                transcript_status VARCHAR(40) NOT NULL DEFAULT 'pending_transcript',
                transcript_attempt_count INTEGER NOT NULL DEFAULT 0,
                transcript_last_attempt_at DATETIME,
                transcript_next_retry_at DATETIME,
                transcript_last_error_code VARCHAR(80),
                transcript_last_error_detail VARCHAR,
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
                video_id, title, published_at, duration,
                transcript_status, transcript_attempt_count,
                transcript_last_attempt_at, transcript_next_retry_at,
                transcript_last_error_code, transcript_last_error_detail,
                created_at
            )
            SELECT
                id, channel_id, person_id, platform, video_url,
                video_id, title, published_at, duration,
                transcript_status, transcript_attempt_count,
                transcript_last_attempt_at, transcript_next_retry_at,
                transcript_last_error_code, transcript_last_error_detail,
                created_at
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
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_video_transcript_status ON video(transcript_status)")
    )
    session.exec(
        text(
            "CREATE INDEX IF NOT EXISTS ix_video_transcript_next_retry_at "
            "ON video(transcript_next_retry_at)"
        )
    )

    # topic_mention single-column indexes
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_topic_mention_video_id ON topic_mention(video_id)")
    )
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_topic_mention_person_id ON topic_mention(person_id)")
    )
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_topic_mention_topic_id ON topic_mention(topic_id)")
    )
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_topic_mention_channel_id ON topic_mention(channel_id)")
    )

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


def _run_postgres_migrations(session: Session) -> None:
    """Add columns that exist in SQLModel models but not yet in the Postgres schema."""
    session.exec(
        text("ALTER TABLE youtube_channel ADD COLUMN IF NOT EXISTS primary_topic_slug VARCHAR(100)")
    )
    session.exec(
        text("ALTER TABLE youtube_channel ADD COLUMN IF NOT EXISTS expected_subtopics VARCHAR")
    )
    session.exec(
        text("ALTER TABLE youtube_channel ADD COLUMN IF NOT EXISTS channel_metadata VARCHAR")
    )
    session.exec(text("ALTER TABLE transcript ADD COLUMN IF NOT EXISTS segments_json VARCHAR"))
    session.exec(
        text(
            "ALTER TABLE video ADD COLUMN IF NOT EXISTS transcript_status "
            "VARCHAR(40) NOT NULL DEFAULT 'pending_transcript'"
        )
    )
    session.exec(
        text(
            "ALTER TABLE video ADD COLUMN IF NOT EXISTS transcript_attempt_count "
            "INTEGER NOT NULL DEFAULT 0"
        )
    )
    session.exec(
        text("ALTER TABLE video ADD COLUMN IF NOT EXISTS transcript_last_attempt_at TIMESTAMP")
    )
    session.exec(
        text("ALTER TABLE video ADD COLUMN IF NOT EXISTS transcript_next_retry_at TIMESTAMP")
    )
    session.exec(
        text("ALTER TABLE video ADD COLUMN IF NOT EXISTS transcript_last_error_code VARCHAR(80)")
    )
    session.exec(
        text("ALTER TABLE video ADD COLUMN IF NOT EXISTS transcript_last_error_detail VARCHAR")
    )
    session.exec(
        text("CREATE INDEX IF NOT EXISTS ix_video_transcript_status ON video(transcript_status)")
    )
    session.exec(
        text(
            "CREATE INDEX IF NOT EXISTS ix_video_transcript_next_retry_at "
            "ON video(transcript_next_retry_at)"
        )
    )
    session.exec(
        text(
            "ALTER TABLE twitter_post ADD COLUMN IF NOT EXISTS thread_length "
            "INTEGER NOT NULL DEFAULT 1"
        )
    )
    session.exec(text("ALTER TABLE twitter_post ADD COLUMN IF NOT EXISTS thread_tweet_ids VARCHAR"))
    session.exec(
        text(
            """
            UPDATE video
            SET transcript_status = 'ready',
                transcript_next_retry_at = NULL,
                transcript_last_error_code = NULL,
                transcript_last_error_detail = NULL
            WHERE EXISTS (
                SELECT 1
                FROM transcript
                WHERE transcript.video_id = video.id
            )
            """
        )
    )
    _merge_dis_siyaset_into_jeopolitik(session)
    _apply_broadened_taxonomy_migration(session)
    session.commit()


def _replace_channel_subtopic_slug(
    session: Session,
    old_slug: str,
    new_slug: str,
) -> None:
    rows = session.exec(
        text(
            """
            SELECT id, expected_subtopics
            FROM youtube_channel
            WHERE expected_subtopics IS NOT NULL
              AND expected_subtopics != ''
            """
        )
    ).all()

    for row in rows:
        channel_id = row[0]
        raw_expected_subtopics = row[1]

        if not isinstance(raw_expected_subtopics, str):
            continue

        try:
            parsed = json.loads(raw_expected_subtopics)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(parsed, list):
            continue

        changed = False
        rewritten: list[str] = []
        for item in parsed:
            if not isinstance(item, str):
                continue

            normalized = item.strip()
            if not normalized:
                continue

            if normalized == old_slug:
                normalized = new_slug
                changed = True

            if normalized not in rewritten:
                rewritten.append(normalized)

        if not changed:
            continue

        session.execute(
            text(
                """
                UPDATE youtube_channel
                SET expected_subtopics = :expected_subtopics
                WHERE id = :channel_id
                """
            ),
            {
                "expected_subtopics": json.dumps(rewritten, ensure_ascii=False),
                "channel_id": channel_id,
            },
        )


# Old slug -> (new slug, new name, new description). Used by the broadened-
# taxonomy migration to rename rows in place (topic_mention.topic_id stays
# valid because we only UPDATE slug/name/description, never delete rows).
_BROADENED_TAXONOMY_RENAMES: list[tuple[str, str, str, str]] = [
    (
        "borsa-istanbul",
        "bist-turk-piyasalari",
        "BIST / Türk Piyasaları",
        "BIST, XU100, XU030, hisse, endeks, banka/sanayi hisseleri, yerli borsa",
    ),
    (
        "dolar",
        "doviz-kur",
        "Döviz ve Kur",
        "USD/TRY, EUR/TRY, GBP/TRY, EUR/USD, DXY, parite, döviz kuru",
    ),
    (
        "bitcoin-kripto",
        "kripto-paralar",
        "Kripto Paralar",
        "Bitcoin/BTC, Ethereum/ETH, altcoin, stablecoin, kripto borsaları",
    ),
    (
        "dow-jones",
        "amerikan-piyasalari",
        "Amerikan Piyasaları",
        "Dow Jones, S&P 500, Nasdaq, Wall Street, ABD borsaları, US futures, Magnificent 7",
    ),
    (
        "petrol",
        "petrol-enerji",
        "Petrol ve Enerji",
        "Brent, WTI, ham petrol, doğalgaz, OPEC, enerji fiyatları",
    ),
    (
        "faiz",
        "faiz-para-politikasi",
        "Faiz ve Para Politikası",
        "TCMB/Fed/ECB faizi, mevduat, repo, politika faizi, tahvil faizleri",
    ),
]


def _rename_topic_to_broadened_taxonomy(
    session: Session,
    old_slug: str,
    new_slug: str,
    new_name: str,
    new_description: str,
) -> None:
    """Rename a topic row in place and rewrite channel expected_subtopics refs.

    If a row with the new slug already exists (e.g. the seed inserted it while
    the old row also lingers), merge by repointing topic_mention and deleting
    the old row — same pattern as _merge_dis_siyaset_into_jeopolitik.
    """
    old_row = session.execute(
        text("SELECT id FROM topic WHERE slug = :slug LIMIT 1"),
        {"slug": old_slug},
    ).first()
    new_row = session.execute(
        text("SELECT id FROM topic WHERE slug = :slug LIMIT 1"),
        {"slug": new_slug},
    ).first()

    _replace_channel_subtopic_slug(session, old_slug=old_slug, new_slug=new_slug)

    old_topic_id = int(old_row[0]) if old_row else None
    new_topic_id = int(new_row[0]) if new_row else None

    if old_topic_id is None and new_topic_id is None:
        return

    if old_topic_id is None:
        # Only the new row exists — ensure its name/description are up to date.
        session.execute(
            text(
                """
                UPDATE topic
                SET name = :name, description = :description
                WHERE id = :id
                """
            ),
            {"id": new_topic_id, "name": new_name, "description": new_description},
        )
        return

    if new_topic_id is None:
        # Only the old row exists — rename in place.
        session.execute(
            text(
                """
                UPDATE topic
                SET slug = :new_slug, name = :name, description = :description
                WHERE id = :id
                """
            ),
            {
                "id": old_topic_id,
                "new_slug": new_slug,
                "name": new_name,
                "description": new_description,
            },
        )
        return

    if old_topic_id == new_topic_id:
        return

    # Both rows exist — merge old into new.
    session.execute(
        text("UPDATE topic_mention SET topic_id = :new_id WHERE topic_id = :old_id"),
        {"new_id": new_topic_id, "old_id": old_topic_id},
    )
    session.execute(
        text(
            """
            UPDATE topic
            SET name = :name, description = :description
            WHERE id = :id
            """
        ),
        {"id": new_topic_id, "name": new_name, "description": new_description},
    )
    session.execute(
        text("DELETE FROM topic WHERE id = :id"),
        {"id": old_topic_id},
    )


def _apply_broadened_taxonomy_migration(session: Session) -> None:
    for old_slug, new_slug, new_name, new_description in _BROADENED_TAXONOMY_RENAMES:
        _rename_topic_to_broadened_taxonomy(
            session,
            old_slug=old_slug,
            new_slug=new_slug,
            new_name=new_name,
            new_description=new_description,
        )


def _merge_dis_siyaset_into_jeopolitik(session: Session) -> None:
    """Normalize legacy `dis-siyaset` references into `jeopolitik`."""
    session.exec(
        text(
            """
            UPDATE youtube_channel
            SET primary_topic_slug = 'jeopolitik'
            WHERE primary_topic_slug = 'dis-siyaset'
            """
        )
    )
    _replace_channel_subtopic_slug(session, old_slug="dis-siyaset", new_slug="jeopolitik")

    dis_row = session.exec(text("SELECT id FROM topic WHERE slug = 'dis-siyaset' LIMIT 1")).first()
    jeopolitik_row = session.exec(
        text("SELECT id FROM topic WHERE slug = 'jeopolitik' LIMIT 1")
    ).first()

    dis_topic_id = int(dis_row[0]) if dis_row else None
    jeopolitik_topic_id = int(jeopolitik_row[0]) if jeopolitik_row else None

    if dis_topic_id is None:
        return

    if jeopolitik_topic_id is None:
        session.execute(
            text(
                """
                UPDATE topic
                SET name = 'Jeopolitik',
                    slug = 'jeopolitik',
                    description = 'Dış siyaset, savaş, uluslararası krizler'
                WHERE id = :dis_topic_id
                """
            ),
            {"dis_topic_id": dis_topic_id},
        )
        return

    if dis_topic_id == jeopolitik_topic_id:
        return

    session.execute(
        text(
            """
            UPDATE topic_mention
            SET topic_id = :jeopolitik_topic_id
            WHERE topic_id = :dis_topic_id
            """
        ),
        {
            "jeopolitik_topic_id": jeopolitik_topic_id,
            "dis_topic_id": dis_topic_id,
        },
    )
    session.execute(
        text("DELETE FROM topic WHERE id = :dis_topic_id"),
        {"dis_topic_id": dis_topic_id},
    )


def run_lightweight_migrations() -> None:
    """Best-effort schema upgrades without Alembic."""
    if engine.dialect.name == "postgresql":
        with Session(engine) as session:
            # Serialize concurrent migration runs; auto-released at transaction end.
            session.exec(text("SELECT pg_advisory_xact_lock(20260501)"))
            _run_postgres_migrations(session)
        return

    if engine.dialect.name != "sqlite":
        return

    with Session(engine) as session:
        if not _sqlite_has_column(session, "video", "channel_id"):
            session.exec(text("ALTER TABLE video ADD COLUMN channel_id INTEGER"))

        if not _sqlite_has_column(session, "video", "transcript_status"):
            session.exec(
                text(
                    "ALTER TABLE video ADD COLUMN transcript_status "
                    "VARCHAR(40) NOT NULL DEFAULT 'pending_transcript'"
                )
            )
        if not _sqlite_has_column(session, "video", "transcript_attempt_count"):
            session.exec(
                text(
                    "ALTER TABLE video ADD COLUMN transcript_attempt_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
        if not _sqlite_has_column(session, "video", "transcript_last_attempt_at"):
            session.exec(text("ALTER TABLE video ADD COLUMN transcript_last_attempt_at DATETIME"))
        if not _sqlite_has_column(session, "video", "transcript_next_retry_at"):
            session.exec(text("ALTER TABLE video ADD COLUMN transcript_next_retry_at DATETIME"))
        if not _sqlite_has_column(session, "video", "transcript_last_error_code"):
            session.exec(
                text("ALTER TABLE video ADD COLUMN transcript_last_error_code VARCHAR(80)")
            )
        if not _sqlite_has_column(session, "video", "transcript_last_error_detail"):
            session.exec(text("ALTER TABLE video ADD COLUMN transcript_last_error_detail VARCHAR"))

        if not _sqlite_has_column(session, "topic_mention", "channel_id"):
            session.exec(text("ALTER TABLE topic_mention ADD COLUMN channel_id INTEGER"))

        if not _sqlite_has_column(session, "transcript", "segments_json"):
            session.exec(text("ALTER TABLE transcript ADD COLUMN segments_json VARCHAR"))

        if not _sqlite_has_column(session, "twitter_post", "thread_length"):
            session.exec(
                text("ALTER TABLE twitter_post ADD COLUMN thread_length INTEGER NOT NULL DEFAULT 1")
            )

        if not _sqlite_has_column(session, "twitter_post", "thread_tweet_ids"):
            session.exec(text("ALTER TABLE twitter_post ADD COLUMN thread_tweet_ids VARCHAR"))

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

        # Add channel-level topic focus columns.
        if not _sqlite_has_column(session, "youtube_channel", "primary_topic_slug"):
            session.exec(
                text("ALTER TABLE youtube_channel ADD COLUMN primary_topic_slug VARCHAR(100)")
            )
        if not _sqlite_has_column(session, "youtube_channel", "expected_subtopics"):
            session.exec(text("ALTER TABLE youtube_channel ADD COLUMN expected_subtopics VARCHAR"))
        if not _sqlite_has_column(session, "youtube_channel", "channel_metadata"):
            session.exec(text("ALTER TABLE youtube_channel ADD COLUMN channel_metadata VARCHAR"))

        _ensure_post_rebuild_indexes(session)
        session.exec(
            text(
                """
                UPDATE video
                SET transcript_status = 'ready',
                    transcript_next_retry_at = NULL,
                    transcript_last_error_code = NULL,
                    transcript_last_error_detail = NULL
                WHERE EXISTS (
                    SELECT 1
                    FROM transcript
                    WHERE transcript.video_id = video.id
                )
                """
            )
        )

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

        _merge_dis_siyaset_into_jeopolitik(session)
        _apply_broadened_taxonomy_migration(session)
        session.commit()
