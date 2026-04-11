from fastapi import APIRouter, status

from src.database import SessionDep
from src.topics import service
from src.topics.dependencies import ValidTopicDep
from src.topics.exceptions import TopicSlugConflict
from src.topics.schemas import TopicCreate, TopicNode, TopicResponse

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


@router.get("/{topic_id}", response_model=TopicResponse)
def get_topic(topic: ValidTopicDep) -> TopicResponse:
    return topic
