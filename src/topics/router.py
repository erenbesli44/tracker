from typing import Annotated, Optional

from fastapi import APIRouter, Query, status

from src.database import SessionDep
from src.topics import service
from src.topics.dependencies import ValidTopicDep, ValidTopicSlugDep
from src.topics.exceptions import TopicSlugConflict
from src.topics.schemas import TopicCreate, TopicNode, TopicOpinionsResponse, TopicResponse

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/", response_model=TopicResponse, status_code=status.HTTP_201_CREATED)
def create_topic(data: TopicCreate, session: SessionDep) -> TopicResponse:
    from slugify import slugify

    if service.get_by_slug(session, slugify(data.name)):
        raise TopicSlugConflict()
    return service.create(session, data)


@router.get("/", response_model=list[TopicResponse])
def list_topics(session: SessionDep) -> list[TopicResponse]:
    return service.list_all(session)


@router.get("/tree", response_model=list[TopicNode])
def get_topic_tree(session: SessionDep) -> list[TopicNode]:
    return service.get_topic_tree(session)


@router.get("/{topic_slug}/opinions", response_model=TopicOpinionsResponse)
def get_topic_opinions(
    topic: ValidTopicSlugDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
    days: Annotated[Optional[int], Query(ge=1, le=365)] = 30,
) -> TopicOpinionsResponse:
    return service.get_topic_opinions_by_channel(
        session, topic, limit_per_channel=limit, days=days
    )


@router.get("/{topic_id}", response_model=TopicResponse)
def get_topic(topic: ValidTopicDep) -> TopicResponse:
    return topic
