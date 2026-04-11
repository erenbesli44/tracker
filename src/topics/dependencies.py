from typing import Annotated

from fastapi import Depends, Path

from src.database import SessionDep
from src.topics import service
from src.topics.exceptions import TopicNotFound
from src.topics.models import Topic


def valid_topic_id(
    topic_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
) -> Topic:
    topic = service.get_by_id(session, topic_id)
    if not topic:
        raise TopicNotFound()
    return topic


ValidTopicDep = Annotated[Topic, Depends(valid_topic_id)]
