from fastapi import Request, status
from fastapi.responses import JSONResponse


class NotFoundError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail


async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": exc.detail},
    )
