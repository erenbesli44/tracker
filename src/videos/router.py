import math
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, status

from src.channels import service as channels_service
from src.channels.exceptions import ChannelNotFound
from src.database import SessionDep
from src.llm import service as llm_service
from src.persons import service as persons_service
from src.persons.exceptions import PersonNotFound
from src import cache
from src.videos import service
from src.videos.dependencies import ValidVideoDep
from src.videos.exceptions import (
    InvalidYouTubeUrl,
    SummaryNotFound,
    TranscriptAlreadyExists,
    TranscriptNotFound,
    VideoUrlConflict,
    YouTubeTranscriptProviderError,
    YouTubeTranscriptUnavailable,
)
from src.videos.schemas import (
    PaginatedVideoResponse,
    TranscriptCreate,
    TranscriptDetailResponse,
    TranscriptFetchRequest,
    TranscriptFetchResponse,
    TranscriptResponse,
    VideoCreate,
    VideoResponse,
    VideoSummaryCreate,
    VideoSummaryResponse,
    VideoUpdate,
    VideoWithTranscript,
)

router = APIRouter(prefix="/videos", tags=["videos"])


def _summary_cache_key(video_id: int) -> str:
    return f"video:summary:{video_id}"


def _summary_to_response(summary) -> VideoSummaryResponse:
    return VideoSummaryResponse(
        id=summary.id,
        video_id=summary.video_id,
        short_summary=summary.short_summary,
        long_summary=summary.long_summary,
        highlights=service.parse_highlights(summary.highlights),
        language=summary.language,
        source=summary.source,
        created_at=summary.created_at,
        updated_at=summary.updated_at,
    )


@router.post(
    "/backfill-published-dates",
    status_code=status.HTTP_200_OK,
    summary="Fetch and update published_at for all videos missing it",
)
def backfill_published_dates(session: SessionDep) -> dict:
    results = service.backfill_published_dates(session)
    updated = sum(1 for r in results if r["status"] == "updated")
    return {
        "total_processed": len(results),
        "updated": updated,
        "results": results,
    }


@router.post("/", response_model=VideoResponse, status_code=status.HTTP_201_CREATED)
def create_video(data: VideoCreate, session: SessionDep) -> VideoResponse:
    if not service.extract_youtube_id(data.video_url):
        raise InvalidYouTubeUrl()
    if service.get_by_url(session, data.video_url):
        raise VideoUrlConflict()

    person = None
    if data.person_id is not None:
        person = persons_service.get_by_id(session, data.person_id)
        if not person:
            raise PersonNotFound()

    if data.channel_id is not None:
        channel = channels_service.get_by_id(session, data.channel_id)
        if not channel:
            raise ChannelNotFound()

    if data.person_id is not None and data.channel_id is None:
        channel = channels_service.ensure_from_person(session, person)
        data.channel_id = channel.id

    return service.create(session, data)


@router.patch("/{video_id}", response_model=VideoResponse)
def update_video(
    data: VideoUpdate,
    video: ValidVideoDep,
    session: SessionDep,
) -> VideoResponse:
    return service.update(session, video, data)


@router.get("/", response_model=PaginatedVideoResponse)
def list_videos(
    session: SessionDep,
    channel_id: Annotated[Optional[int], Query(ge=1)] = None,
    person_id: Annotated[Optional[int], Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedVideoResponse:
    offset = (page - 1) * size
    if channel_id:
        total = service.count_by_channel(session, channel_id)
        items = service.list_by_channel(session, channel_id, limit=size, offset=offset)
    elif person_id:
        total = service.count_by_person(session, person_id)
        items = service.list_by_person(session, person_id, limit=size, offset=offset)
    else:
        total = service.count_all(session)
        items = service.list_all(session, limit=size, offset=offset)
    return PaginatedVideoResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=math.ceil(total / size) if total else 0,
    )


@router.get("/{video_id}", response_model=VideoWithTranscript)
def get_video(video: ValidVideoDep, session: SessionDep) -> VideoWithTranscript:
    transcript = service.get_transcript(session, video.id)
    result = VideoWithTranscript.model_validate(video)
    if transcript:
        result.transcript = TranscriptResponse.model_validate(transcript)
    return result


@router.get("/{video_id}/transcript", response_model=TranscriptDetailResponse)
def get_video_transcript(video: ValidVideoDep, session: SessionDep) -> TranscriptDetailResponse:
    transcript = service.get_transcript(session, video.id)
    if not transcript:
        raise TranscriptNotFound()
    return TranscriptDetailResponse.model_validate(transcript)


@router.post(
    "/{video_id}/transcript",
    response_model=TranscriptResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_transcript(
    data: TranscriptCreate,
    video: ValidVideoDep,
    session: SessionDep,
) -> TranscriptResponse:
    if service.get_transcript(session, video.id):
        raise TranscriptAlreadyExists()
    return service.add_transcript(session, video, data)


@router.post("/{video_id}/summary", response_model=VideoSummaryResponse)
def upsert_video_summary(
    data: VideoSummaryCreate,
    video: ValidVideoDep,
    session: SessionDep,
) -> VideoSummaryResponse:
    summary = service.get_summary(session, video.id)
    if summary:
        updated = service.update_summary(session, summary, data)
        response = _summary_to_response(updated)
    else:
        created = service.add_summary(session, video, data)
        response = _summary_to_response(created)
    cache.set_cached(_summary_cache_key(video.id), response.model_dump(mode="json"))
    return response


@router.get("/{video_id}/summary", response_model=VideoSummaryResponse)
def get_video_summary(video: ValidVideoDep, session: SessionDep) -> VideoSummaryResponse:
    key = _summary_cache_key(video.id)
    cached = cache.get_cached(key)
    if cached:
        return VideoSummaryResponse.model_validate(cached)

    summary = service.get_summary(session, video.id)
    if not summary:
        raise SummaryNotFound()
    response = _summary_to_response(summary)
    cache.set_cached(key, response.model_dump(mode="json"))
    return response


@router.post(
    "/{video_id}/transcript/fetch",
    response_model=TranscriptFetchResponse,
    status_code=status.HTTP_200_OK,
    summary="Fetch transcript from YouTube and store it",
)
def fetch_transcript(
    video: ValidVideoDep,
    session: SessionDep,
    data: TranscriptFetchRequest = Body(default_factory=TranscriptFetchRequest),
) -> TranscriptFetchResponse:
    existing = service.get_transcript(session, video.id)
    if existing and not data.overwrite_existing:
        raise TranscriptAlreadyExists()

    try:
        fetched = service.fetch_transcript_from_youtube(video.video_id, data.languages)
    except service.YouTubeTranscriptFetchError as exc:
        service.record_transcript_fetch_failure(
            session,
            video,
            code=exc.code,
            detail=exc.detail,
        )
        if exc.code == "provider_error":
            raise YouTubeTranscriptProviderError() from exc
        raise YouTubeTranscriptUnavailable(exc.detail) from exc

    if existing:
        transcript = service.update_transcript(
            session,
            existing,
            raw_text=fetched["full_text"],
            language=fetched["language"],
            segments=fetched["segments"],
        )
    else:
        transcript = service.add_transcript(
            session,
            video,
            TranscriptCreate(
                raw_text=fetched["full_text"],
                language=fetched["language"],
                segments=fetched["segments"],
            ),
        )

    return TranscriptFetchResponse(
        transcript=TranscriptDetailResponse.model_validate(transcript),
        source="youtube",
        fetched_language=fetched["language"],
        is_generated=fetched["is_generated"],
        segment_count=len(fetched["segments"]),
        languages_tried=fetched["languages_tried"],
    )


@router.post(
    "/{video_id}/economic-thesis",
    status_code=status.HTTP_200_OK,
    summary="Extract economic thesis from video transcript using LLM",
)
def get_economic_thesis(
    video: ValidVideoDep,
    session: SessionDep,
) -> dict[str, Any]:
    transcript = service.get_transcript(session, video.id)
    if not transcript:
        raise TranscriptNotFound()

    channel_name = "unknown"
    if video.channel_id:
        channel = channels_service.get_by_id(session, video.channel_id)
        if channel:
            channel_name = channel.name

    speaker_name = channel_name
    if video.person_id:
        person = persons_service.get_by_id(session, video.person_id)
        if person:
            speaker_name = person.name

    try:
        return llm_service.generate_economic_thesis_json(
            source_platform=video.platform,
            channel_name=channel_name,
            speaker_name=speaker_name,
            video_title=video.title or "",
            published_at=video.published_at.isoformat() if video.published_at else "",
            source_url=video.video_url,
            transcript=transcript.raw_text,
        )
    except llm_service.LLMGenerationError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
