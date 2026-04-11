from fastapi import APIRouter, status

from src.classification import service
from src.classification.constants import (
    CLASSIFICATION_SYSTEM_PROMPT,
    CLASSIFICATION_USER_PROMPT_TEMPLATE,
)
from src.classification.schemas import (
    ClassificationRequest,
    ClassificationResponse,
    PromptTemplateResponse,
)
from src.database import SessionDep
from src.videos.dependencies import ValidVideoDep

router = APIRouter(prefix="/videos", tags=["classification"])


@router.post(
    "/{video_id}/classify",
    response_model=ClassificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit classification results for a video",
    description="Replace any existing classification with a new one.",
)
def classify_video(
    data: ClassificationRequest,
    video: ValidVideoDep,
    session: SessionDep,
) -> ClassificationResponse:
    return service.classify_video(session, video, data)


@router.get(
    "/{video_id}/classification",
    response_model=ClassificationResponse,
    summary="Get classification results for a video",
)
def get_classification(video: ValidVideoDep, session: SessionDep) -> ClassificationResponse:
    return service.get_classification(session, video.id)


@router.get(
    "/classification/prompt",
    response_model=PromptTemplateResponse,
    tags=["classification"],
    summary="Get the LLM classification prompt template",
)
def get_prompt_template() -> PromptTemplateResponse:
    return PromptTemplateResponse(
        system_prompt=CLASSIFICATION_SYSTEM_PROMPT,
        user_prompt_template=CLASSIFICATION_USER_PROMPT_TEMPLATE,
    )
