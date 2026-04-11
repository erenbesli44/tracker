from fastapi import APIRouter, status
from pydantic import Field

from src.database import SessionDep
from src.ingestion import service
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
