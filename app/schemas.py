from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TodoBase(BaseModel):
    title: str
    description: str | None = None
    completed: bool = False

    # Phase 1 (Smart Task Input): AI-extracted metadata
    due_date: datetime | None = None
    assignees: list[str] | None = None
    subtasks: list[str] | None = None
    context: str | None = None
    effort_hours: float | None = Field(default=None, ge=0)
    category: str | None = None
    project: str | None = None
    area_of_focus: str | None = None
    skills_required: list[str] | None = None
    urgency: str | None = Field(default=None, pattern="^(low|medium|high)$")
    priority_score: float | None = Field(default=None, ge=0, le=1)
    impact_score: float | None = Field(default=None, ge=0, le=1)
    feasibility_score: float | None = Field(default=None, ge=0, le=1)
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    created_from: str = "ui"

    # Actual behaviour (Phase 2 prerequisite). `effort_hours` stays the AI estimate;
    # this is what really happened. `completed_at` is server-set, so it is exposed
    # on responses only (see TodoResponse).
    actual_effort_hours: float | None = Field(default=None, ge=0)

class TodoCreate(TodoBase):
    pass

class TodoUpdate(BaseModel):
    """Partial update: only fields explicitly present in the body are applied."""

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    completed: bool | None = None
    due_date: datetime | None = None
    assignees: list[str] | None = None
    subtasks: list[str] | None = None
    context: str | None = None
    effort_hours: float | None = Field(default=None, ge=0)
    category: str | None = None
    project: str | None = None
    area_of_focus: str | None = None
    skills_required: list[str] | None = None
    urgency: str | None = Field(default=None, pattern="^(low|medium|high)$")
    priority_score: float | None = Field(default=None, ge=0, le=1)
    impact_score: float | None = Field(default=None, ge=0, le=1)
    feasibility_score: float | None = Field(default=None, ge=0, le=1)
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    created_from: str | None = None
    actual_effort_hours: float | None = Field(default=None, ge=0)

class TodoResponse(TodoBase):
    id: int
    created_at: datetime
    completed_at: datetime | None = None  # server-set when the task is completed

    model_config = ConfigDict(from_attributes=True)


# Phase 1 (Smart Task Input): AI task parsing
class TaskParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value):
        if not isinstance(value, str):
            return value  # let type validation produce its own error
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be empty or whitespace-only")
        return stripped


class TaskParsedExtracted(BaseModel):
    title: str | None = None
    due_date: datetime | None = None
    assignees: list[str] | None = None
    subtasks: list[str] | None = None
    effort_hours: float | None = Field(default=None, ge=0)
    context: str | None = None


class TaskParsedInferred(BaseModel):
    category: str | None = None
    priority_score: float | None = Field(default=None, ge=0, le=1)


class TaskClarification(BaseModel):
    required: bool = False
    question: str | None = None
    missing_fields: list[str] = Field(default_factory=list)


class TaskParseResult(BaseModel):
    text: str
    extracted: TaskParsedExtracted
    inferred: TaskParsedInferred
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    clarification: TaskClarification = Field(default_factory=TaskClarification)


# Priority 2 (AI Categorization)
class TaskCategorizeRequest(BaseModel):
    task_id: int


class TaskCategorizationSuggestion(BaseModel):
    category: str | None = None
    project: str | None = None
    area_of_focus: str | None = None
    urgency: str | None = Field(default=None, pattern="^(low|medium|high)$")
    skills_required: list[str] = Field(default_factory=list)


class TaskCategorizationResult(BaseModel):
    task_id: int
    suggested: TaskCategorizationSuggestion
    reasons: dict[str, str] = Field(default_factory=dict)
    confidence_level: float | None = Field(default=None, ge=0, le=1)


# Priority 3 (Intelligent Prioritization)
class TaskPrioritizeRequest(BaseModel):
    task_ids: list[int] | None = None
    available_hours: float | None = Field(default=None, ge=0)


class TaskPriorityScore(BaseModel):
    task_id: int
    priority_score: float = Field(ge=0, le=1)
    impact_score: float | None = Field(default=None, ge=0, le=1)
    feasibility_score: float | None = Field(default=None, ge=0, le=1)
    reason: str | None = None


class WorkloadAnalysis(BaseModel):
    available_hours: float | None = None
    required_hours: float
    fits: bool | None = None
    message: str


class TaskPrioritizeResult(BaseModel):
    items: list[TaskPriorityScore]
    workload: WorkloadAnalysis
    suggestions: list[str] = Field(default_factory=list)
    confidence_level: float | None = Field(default=None, ge=0, le=1)


# Priority 4 (Task Decomposition)
class TaskBreakdownResult(BaseModel):
    task_id: int
    subtasks: list[str] = Field(min_length=1, max_length=20)
    confidence_level: float | None = Field(default=None, ge=0, le=1)

    @field_validator("subtasks")
    @classmethod
    def _clean_subtasks(cls, steps: list[str]) -> list[str]:
        cleaned = [step.strip() for step in steps if step.strip()]
        if not cleaned:
            raise ValueError("subtasks must contain at least one non-empty step")
        return cleaned


# Priority 5 (Suggested Next Action)
class TaskNextActionResult(BaseModel):
    task_id: int
    actions: list[str] = Field(min_length=1, max_length=5)
    confidence_level: float | None = Field(default=None, ge=0, le=1)

    @field_validator("actions")
    @classmethod
    def _clean_actions(cls, items: list[str]) -> list[str]:
        cleaned = [item.strip() for item in items if item.strip()]
        if not cleaned:
            raise ValueError("actions must contain at least one non-empty step")
        return cleaned


# Priority 6 (Smart Reminders) - deterministic, no AI involved (Rules section 5.6)
class Reminder(BaseModel):
    task_id: int
    title: str
    due_date: datetime
    level: str = Field(pattern="^(info|warning|urgent)$")
    message: str
