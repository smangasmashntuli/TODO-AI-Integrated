from fastapi import APIRouter, Depends, HTTPException, status

from . import schemas, services

router = APIRouter(
    prefix="/api/tasks",
    tags=["ai-tasks"],
)


@router.post(
    "/parse",
    response_model=schemas.TaskParseResult,
    summary="Parse a natural-language task into a structured suggestion",
)
def parse_task(
    payload: schemas.TaskParseRequest,
    service: services.TaskParsingService = Depends(services.get_task_parsing_service),
):
    """Parse a natural-language task into a structured suggestion."""
    try:
        return service.parse(payload.text)
    except services.AIProviderNotConfiguredError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI task parsing is not configured: GEMINI_API_KEY is not set.",
        ) from None
    except services.InvalidAIResponseError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI returned a malformed or invalid response and it was rejected.",
        ) from None
    except services.AIProviderError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI provider is unavailable. Please try again later.",
        ) from None