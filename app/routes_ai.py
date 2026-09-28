from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, schemas, services
from .database import get_db

router = APIRouter(
    prefix="/api/tasks",
    tags=["ai-tasks"],
)


@contextmanager
def _ai_errors(not_configured_detail: str):
    """Map AI service failures to HTTP responses (Rule 6 / section 13)."""
    try:
        yield
    except services.AIProviderNotConfiguredError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, not_configured_detail) from None
    except services.InvalidAIResponseError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI returned a malformed or invalid response and it was rejected.",
        ) from None
    except services.AIProviderQuotaError:
        # Quota/billing limit: retrying will not help; tell the operator what to fix.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "The AI usage quota or billing limit has been reached. Check the GEMINI_API_KEY plan, or try again later.",
            headers={"Retry-After": "60"},
        ) from None
    except services.AIProviderBusyError:
        # Upstream overload after retries: retryable, not a client error.
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "The AI model is temporarily busy (high demand). Please try again in a few seconds.",
            headers={"Retry-After": "5"},
        ) from None
    except services.AIProviderTimeoutError:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            "The AI provider timed out. Please try again.",
        ) from None
    except services.AIProviderError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "AI provider is unavailable. Please try again later.",
        ) from None


@router.post(
    "/parse",
    response_model=schemas.TaskParseResult,
    summary="Parse a natural-language task into a structured suggestion",
)
def parse_task(
    payload: schemas.TaskParseRequest,
    service: services.TaskParsingService = Depends(services.get_task_parsing_service),
):
    """Phase 1: parse natural language into a structured suggestion (no DB writes)."""
    with _ai_errors("AI task parsing is not configured: GEMINI_API_KEY is not set."):
        return service.parse(payload.text)


@router.post(
    "/categorize",
    response_model=schemas.TaskCategorizationResult,
    summary="Categorize an existing task into explainable suggestions",
)
def categorize_task(
    payload: schemas.TaskCategorizeRequest,
    db: Session = Depends(get_db),
    service: services.TaskParsingService = Depends(services.get_task_parsing_service),
):
    """Priority 2: explainable categorization suggestions; never modifies the task."""
    todo = db.scalars(select(models.Todo).where(models.Todo.id == payload.task_id)).first()
    if not todo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Todo not found")

    with _ai_errors("AI task categorization is not configured: GEMINI_API_KEY is not set."):
        return service.categorize(todo.id, todo.title, todo.description, todo.context)


@router.post(
    "/prioritize",
    response_model=schemas.TaskPrioritizeResult,
    summary="Rank open tasks and analyse workload",
)
def prioritize_tasks(
    payload: schemas.TaskPrioritizeRequest,
    db: Session = Depends(get_db),
    service: services.TaskParsingService = Depends(services.get_task_parsing_service),
):
    """Priority 3: rank open tasks and report a deterministic workload analysis.

    Ranks the requested tasks (all open tasks when task_ids is omitted). Nothing
    is modified: the client confirms and persists scores via the existing update
    flow (Rule 7). The capacity comparison is computed by the application, not
    by the AI.
    """
    query = select(models.Todo)
    if payload.task_ids is None:
        query = query.where(models.Todo.completed.is_(False))
    else:
        query = query.where(models.Todo.id.in_(payload.task_ids))
    tasks = db.scalars(query).all()
    if not tasks:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No tasks found to prioritize")

    with _ai_errors("AI task prioritization is not configured: GEMINI_API_KEY is not set."):
        return service.prioritize(tasks, payload.available_hours)


@router.post(
    "/{task_id}/breakdown",
    response_model=schemas.TaskBreakdownResult,
    summary="Suggest a step-by-step breakdown of a task",
)
def breakdown_task(
    task_id: int,
    db: Session = Depends(get_db),
    service: services.TaskParsingService = Depends(services.get_task_parsing_service),
):
    """Priority 4: suggest an ordered breakdown into steps.

    Suggestions only: the task is never modified here (Rule 7). The client
    reviews the steps and persists accepted ones via the existing update flow.
    """
    todo = db.scalars(select(models.Todo).where(models.Todo.id == task_id)).first()
    if not todo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Todo not found")

    with _ai_errors("AI task decomposition is not configured: GEMINI_API_KEY is not set."):
        return service.breakdown(todo.id, todo.title, todo.description, todo.context)


@router.post(
    "/{task_id}/next-action",
    response_model=schemas.TaskNextActionResult,
    summary="Suggest a small number of next actions for a task",
)
def next_action_task(
    task_id: int,
    db: Session = Depends(get_db),
    service: services.TaskParsingService = Depends(services.get_task_parsing_service),
):
    """Priority 5: suggest 1-3 immediately actionable next steps.

    Suggestions only: the task is never modified here (Rule 7). The user picks
    an action and applies it through the existing update flow.
    """
    todo = db.scalars(select(models.Todo).where(models.Todo.id == task_id)).first()
    if not todo:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Todo not found")

    with _ai_errors("AI next-action suggestions are not configured: GEMINI_API_KEY is not set."):
        return service.next_action(
            todo.id, todo.title, todo.description, todo.context, todo.subtasks
        )


@router.get(
    "/reminders",
    response_model=list[schemas.Reminder],
    summary="List smart reminders for open tasks due soon",
)
def list_reminders(db: Session = Depends(get_db)):
    """Priority 6: deterministic reminders with escalation (Rules section 5.6).

    No AI call: deadline, status, context and urgency are evaluated by simple
    rules. In-app only - no notification channels exist yet (Rules section 5.6).
    """
    tasks = db.scalars(
        select(models.Todo).where(models.Todo.completed.is_(False))
    ).all()
    return services.build_reminders(tasks)


@router.get(
    "/learning",
    response_model=schemas.LearningProfile,
    summary="Learned patterns from completed-task history",
)
def learning_profile(db: Session = Depends(get_db)):
    """Priority 7: read-only learning profile (Rules: Historical Learning).

    Deterministic, no AI call. Only completed tasks are considered; below
    MIN_HISTORY_FOR_LEARNING the profile reports confidence='insufficient_data'
    with no patterns rather than inventing one.
    """
    completed = db.scalars(
        select(models.Todo).where(models.Todo.completed.is_(True))
    ).all()
    return services.build_learning_profile(completed)
