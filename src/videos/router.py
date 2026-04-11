from typing import Annotated, Optional

from fastapi import APIRouter, Body, Query, status

from src.channels import service as channels_service
from src.channels.exceptions import ChannelNotFound
from src.database import SessionDep
from src.persons import service as persons_service
from src.persons.exceptions import PersonNotFound
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
    TranscriptCreate,
    TranscriptDetailResponse,
    TranscriptFetchRequest,
    TranscriptFetchResponse,
    TranscriptResponse,
    VideoCreate,
    VideoResponse,
    VideoSummaryCreate,
    VideoSummaryResponse,
    VideoWithTranscript,
)

router = APIRouter(prefix="/videos", tags=["videos"])


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


@router.get("/", response_model=list[VideoResponse])
def list_videos(
    session: SessionDep,
    channel_id: Annotated[Optional[int], Query(ge=1)] = None,
    person_id: Annotated[Optional[int], Query(ge=1)] = None,
) -> list[VideoResponse]:
    if channel_id:
        return service.list_by_channel(session, channel_id)
    if person_id:
        return service.list_by_person(session, person_id)
    return service.list_all(session)


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
        return _summary_to_response(updated)
    created = service.add_summary(session, video, data)
    return _summary_to_response(created)


@router.get("/{video_id}/summary", response_model=VideoSummaryResponse)
def get_video_summary(video: ValidVideoDep, session: SessionDep) -> VideoSummaryResponse:
    summary = service.get_summary(session, video.id)
    if not summary:
        raise SummaryNotFound()
    return _summary_to_response(summary)


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
        if exc.code == "provider_error":
            raise YouTubeTranscriptProviderError() from exc
        raise YouTubeTranscriptUnavailable(exc.detail) from exc

    if existing:
        transcript = service.update_transcript(
            session,
            existing,
            raw_text=fetched["full_text"],
            language=fetched["language"],
        )
    else:
        transcript = service.add_transcript(
            session,
            video,
            TranscriptCreate(
                raw_text=fetched["full_text"],
                language=fetched["language"],
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
