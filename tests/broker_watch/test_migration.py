"""Lightweight sqlite migration for broker watch tables created by OI-027 step 1."""

from sqlalchemy import text
from sqlmodel import Session, create_engine

from src.database import _add_broker_watch_step2_columns, _sqlite_has_column

STEP1_TABLES = [
    """
    CREATE TABLE model_portfolio_snapshot (
        id INTEGER PRIMARY KEY,
        broker_id INTEGER NOT NULL,
        document_id INTEGER NOT NULL,
        as_of DATE NOT NULL,
        items_json VARCHAR NOT NULL,
        created_at DATETIME NOT NULL
    )
    """,
    """
    CREATE TABLE broker_watch_source_result (
        id INTEGER PRIMARY KEY,
        run_id INTEGER NOT NULL,
        broker_slug VARCHAR(100) NOT NULL,
        broker_name VARCHAR(255) NOT NULL,
        status VARCHAR(20) NOT NULL,
        documents_seen INTEGER NOT NULL,
        documents_new INTEGER NOT NULL,
        events_new INTEGER NOT NULL,
        snapshots_new INTEGER NOT NULL,
        errors_count INTEGER NOT NULL,
        error_detail VARCHAR
    )
    """,
]
STEP2_COLUMNS = [
    ("model_portfolio_snapshot", "basis"),
    ("broker_watch_source_result", "documents_skipped_robots"),
    ("broker_watch_source_result", "events_flagged"),
]


def test_step2_columns_are_added_to_step1_sqlite_tables():
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        for ddl in STEP1_TABLES:
            session.exec(text(ddl))
        session.exec(
            text(
                "INSERT INTO model_portfolio_snapshot "
                "(broker_id, document_id, as_of, items_json, created_at) "
                "VALUES (1, 1, '2026-09-14', '[]', '2026-09-14 00:00:00')"
            )
        )
        session.exec(
            text(
                "INSERT INTO broker_watch_source_result (run_id, broker_slug, broker_name, "
                "status, documents_seen, documents_new, events_new, snapshots_new, errors_count) "
                "VALUES (1, 'gedik-yatirim', 'Gedik', 'success', 1, 1, 0, 1, 0)"
            )
        )
        assert not any(_sqlite_has_column(session, t, c) for t, c in STEP2_COLUMNS)

        _add_broker_watch_step2_columns(session)
        _add_broker_watch_step2_columns(session)  # idempotent
        session.commit()

        assert all(_sqlite_has_column(session, t, c) for t, c in STEP2_COLUMNS)
        basis = session.exec(text("SELECT basis FROM model_portfolio_snapshot")).one()[0]
        assert basis == "current"
        counters = session.exec(
            text("SELECT documents_skipped_robots, events_flagged FROM broker_watch_source_result")
        ).one()
        assert tuple(counters) == (0, 0)
