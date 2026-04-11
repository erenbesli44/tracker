from fastapi import APIRouter, status

from src.database import SessionDep
from src.ingestion import service
from src.ingestion.schemas import (
    IngestionYoutubeChannelRunRequest,
    IngestionYoutubeChannelRunResponse,
    IngestionYoutubeRequest,
    IngestionYoutubeResponse,
)

router = APIRouter(prefix="/ingestions", tags=["ingestions"])


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
