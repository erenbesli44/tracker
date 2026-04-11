from fastapi import APIRouter, status

from src.channels import service
from src.channels.dependencies import ValidChannelDep
from src.channels.exceptions import ChannelSlugConflict
from src.channels.schemas import ChannelCreate, ChannelResponse, ChannelUpdate
from src.database import SessionDep

router = APIRouter(prefix="/channels", tags=["channels"])


@router.post("/", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
def create_channel(data: ChannelCreate, session: SessionDep) -> ChannelResponse:
    from slugify import slugify

    if service.get_by_slug(session, slugify(data.name)):
        raise ChannelSlugConflict()
    return service.create(session, data)


@router.get("/", response_model=list[ChannelResponse])
def list_channels(session: SessionDep) -> list[ChannelResponse]:
    return service.list_all(session)


@router.get("/{channel_id}", response_model=ChannelResponse)
def get_channel(channel: ValidChannelDep) -> ChannelResponse:
    return channel


@router.patch("/{channel_id}", response_model=ChannelResponse)
def update_channel(
    data: ChannelUpdate,
    channel: ValidChannelDep,
    session: SessionDep,
) -> ChannelResponse:
    return service.update(session, channel, data)
