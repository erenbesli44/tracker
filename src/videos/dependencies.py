from typing import Annotated

from fastapi import Depends, Path

from src.database import SessionDep
from src.videos import service
from src.videos.exceptions import VideoNotFound
from src.videos.models import Video


def valid_video_id(
    video_id: Annotated[int, Path(ge=1)],
    session: SessionDep,
) -> Video:
    video = service.get_by_id(session, video_id)
    if not video:
        raise VideoNotFound()
    return video


ValidVideoDep = Annotated[Video, Depends(valid_video_id)]
