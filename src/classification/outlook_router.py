"""Operator endpoints for the instrument-outlook backfill (API-key protected)."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, status

from src.classification import outlook
from src.database import SessionDep, engine

router = APIRouter(prefix="/outlook", tags=["outlook"])


@router.post(
    "/backfill",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Classify pending instrument mentions in the background (one LLM call per video)",
)
def start_backfill(
    background_tasks: BackgroundTasks,
    limit: Annotated[int, Query(ge=1, le=2000)] = 50,
    workers: Annotated[int, Query(ge=1, le=6)] = 3,
) -> dict:
    state = outlook.backfill_state()
    if state.get("running"):
        return {"status": "already_running", **state}
    background_tasks.add_task(outlook.run_backfill, engine, limit=limit, workers=workers)
    return {"status": "accepted", "limit": limit, "workers": workers}


@router.get("/backfill", summary="Backfill progress and classification coverage")
def backfill_status(session: SessionDep) -> dict:
    return {
        "run": outlook.backfill_state(),
        "coverage": outlook.coverage(session),
        "attribution": outlook.attribution_coverage(session),
    }
