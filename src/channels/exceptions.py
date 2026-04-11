from fastapi import HTTPException, status


class ChannelNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )


class ChannelSlugConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="A channel with this name already exists",
        )
