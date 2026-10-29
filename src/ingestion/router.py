from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, status
from pydantic import Field

from src.database import SessionDep, engine
from src.ingestion import fill_missing_topics, service
from src.ingestion.schemas import (
    IngestionYoutubeChannelRunRequest,
    IngestionYoutubeChannelRunResponse,
    IngestionYoutubeRequest,
    IngestionYoutubeResponse,
)
from src.models import CustomModel

router = APIRouter(prefix="/ingestions", tags=["ingestions"])


class IngestByUrlRequest(CustomModel):
    url: str = Field(min_length=1, max_length=500)
    transcript_languages: list[str] | None = None


@router.post(
    "/youtube/url",
    response_model=IngestionYoutubeResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest a YouTube video by URL — fetches transcript and runs full pipeline automatically",
)
def ingest_youtube_by_url(
    data: IngestByUrlRequest,
    session: SessionDep,
) -> IngestionYoutubeResponse:
    return service.ingest_youtube_by_url(session, data.url, data.transcript_languages)


@router.post(
    "/youtube",
    response_model=IngestionYoutubeResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest one influencer YouTube video with transcript and optional analysis",
)
def ingest_youtube(
    data: IngestionYoutubeRequest,
    session: SessionDep,
) -> IngestionYoutubeResponse:
    return service.ingest_youtube(session, data)


@router.post(
    "/youtube/channel",
    response_model=IngestionYoutubeChannelRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Ingest latest N videos for a YouTube channel",
)
def ingest_youtube_channel(
    data: IngestionYoutubeChannelRunRequest,
    session: SessionDep,
) -> IngestionYoutubeChannelRunResponse:
    return service.ingest_youtube_channel(session, data)


@router.post(
    "/fill-missing-topics",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-analyse recent videos on the full transcript and add only missing topics",
)
def start_fill_missing_topics(
    background_tasks: BackgroundTasks,
    days: Annotated[int, Query(ge=1, le=90)] = 14,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    workers: Annotated[int, Query(ge=1, le=4)] = 2,
    dry_run: bool = True,
) -> dict:
    state = fill_missing_topics.fill_state()
    if state.get("running"):
        return {"status": "already_running", **state}
    background_tasks.add_task(
        fill_missing_topics.run_fill,
        engine,
        days=days,
        limit=limit,
        workers=workers,
        dry_run=dry_run,
    )
    return {
        "status": "accepted",
        "days": days,
        "limit": limit,
        "workers": workers,
        "dry_run": dry_run,
    }


@router.get("/fill-missing-topics", summary="Progress of the fill-missing-topics run")
def fill_missing_topics_status() -> dict:
    return fill_missing_topics.fill_state()
