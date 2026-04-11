from typing import Annotated, Optional

from fastapi import APIRouter, Path, Query, status

from src.channels.dependencies import ValidChannelDep
from src.database import SessionDep
from src.persons.dependencies import ValidPersonDep
from src.timeline import service
from src.timeline.schemas import PersonOverviewResponse, TimelineResponse
from src.topics.exceptions import TopicNotFound

router = APIRouter(tags=["timeline"])


@router.get(
    "/persons/{person_id}/topics/overview",
    response_model=PersonOverviewResponse,
    summary="Get all topics a person has discussed with their latest opinions",
)
def get_person_overview(
    person: ValidPersonDep,
    session: SessionDep,
) -> PersonOverviewResponse:
    return service.get_person_overview(session, person.id)


@router.get(
    "/persons/{person_id}/topics/{topic_id}/timeline",
    response_model=TimelineResponse,
    summary="Get timeline of a person's opinions on a specific topic",
)
def get_topic_timeline(
    person: ValidPersonDep,
    topic_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    result = service.get_topic_timeline(session, person.id, topic_id, limit, offset)
    if result is None:
        raise TopicNotFound()
    return result


@router.get(
    "/channels/{channel_id}/topics/overview",
    response_model=PersonOverviewResponse,
    summary="Get all topics a channel has discussed with latest opinions",
)
def get_channel_overview(
    channel: ValidChannelDep,
    session: SessionDep,
) -> PersonOverviewResponse:
    # channel validity is guaranteed by dependency
    return service.get_channel_overview(session, channel.id)


@router.get(
    "/channels/{channel_id}/topics/{topic_id}/timeline",
    response_model=TimelineResponse,
    summary="Get timeline of a channel's opinions on a specific topic",
)
def get_channel_topic_timeline(
    channel: ValidChannelDep,
    topic_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TimelineResponse:
    result = service.get_channel_topic_timeline(session, channel.id, topic_id, limit, offset)
    if result is None:
        raise TopicNotFound()
    return result
