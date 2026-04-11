from fastapi import HTTPException, status


class TopicNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Topic not found",
        )


class TopicSlugConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="A topic with this slug already exists",
        )
