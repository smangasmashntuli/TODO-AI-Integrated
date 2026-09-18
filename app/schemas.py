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
    priority_score: float | None = Field(default=None, ge=0, le=1)
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    created_from: str = "ui"

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
    priority_score: float | None = Field(default=None, ge=0, le=1)
    confidence_level: float | None = Field(default=None, ge=0, le=1)
    created_from: str | None = None

class TodoResponse(TodoBase):
    id: int
    created_at: datetime

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
