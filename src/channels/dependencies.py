from typing import Annotated

from fastapi import Depends, Path

from src.channels import service
from src.channels.exceptions import ChannelNotFound
from src.channels.models import YouTubeChannel
from src.database import SessionDep


def valid_channel_id(
    channel_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
) -> YouTubeChannel:
    channel = service.get_by_id(session, channel_id)
    if not channel:
        raise ChannelNotFound()
    return channel


ValidChannelDep = Annotated[YouTubeChannel, Depends(valid_channel_id)]
