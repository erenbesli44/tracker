from fastapi import HTTPException, status


class ClassificationNotFound(HTTPException):
    def __init__(self, video_id: int) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No classification found for video {video_id}.",
        )
