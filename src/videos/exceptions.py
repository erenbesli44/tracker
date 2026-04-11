from fastapi import HTTPException, status


class VideoNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found",
        )


class VideoUrlConflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="A video with this URL already exists",
        )


class TranscriptAlreadyExists(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="A transcript already exists for this video",
        )


class TranscriptNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transcript not found for this video",
        )


class SummaryNotFound(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Summary not found for this video",
        )


class InvalidYouTubeUrl(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not extract a valid YouTube video ID from the provided URL",
        )


class YouTubeTranscriptUnavailable(HTTPException):
    def __init__(
        self,
        detail: str = "No transcript is available for this video in the requested languages.",
    ) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=detail,
        )


class YouTubeTranscriptProviderError(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch transcript from YouTube provider",
        )
