"""Lightweight sqlite migration for tables created by OI-027 step 1 (no basis column)."""

from sqlalchemy import text
from sqlmodel import Session, create_engine

from src.database import _add_model_portfolio_snapshot_basis, _sqlite_has_column

STEP1_TABLE = """
CREATE TABLE model_portfolio_snapshot (
    id INTEGER PRIMARY KEY,
    broker_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    as_of DATE NOT NULL,
    items_json VARCHAR NOT NULL,
    created_at DATETIME NOT NULL
)
"""


def test_snapshot_basis_column_is_added_to_a_step1_sqlite_table():
    engine = create_engine("sqlite://")
    with Session(engine) as session:
        session.exec(text(STEP1_TABLE))
        session.exec(
            text(
                "INSERT INTO model_portfolio_snapshot "
                "(broker_id, document_id, as_of, items_json, created_at) "
                "VALUES (1, 1, '2026-09-14', '[]', '2026-09-14 00:00:00')"
            )
        )
        assert not _sqlite_has_column(session, "model_portfolio_snapshot", "basis")

        _add_model_portfolio_snapshot_basis(session)
        _add_model_portfolio_snapshot_basis(session)  # idempotent
        session.commit()

        assert _sqlite_has_column(session, "model_portfolio_snapshot", "basis")
        rows = session.exec(text("SELECT basis FROM model_portfolio_snapshot")).all()
        assert [row[0] for row in rows] == ["current"]
