import json
import logging
import os
import time
from datetime import datetime

from . import schemas

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Bounded provider call (Rule 13: timeouts must be handled). HttpOptions.timeout is milliseconds.
_GEMINI_TIMEOUT_MS = int(float(os.getenv("GEMINI_TIMEOUT_SECONDS", "30")) * 1000)
# The model is occasionally overloaded ("high demand"); retry before giving up.
_MAX_ATTEMPTS = 3
_RETRY_DELAY_SECONDS = 1.0
# Upstream failure markers. Quota/billing limits are NOT retried: they do not clear in seconds.
_TIMEOUT_MARKERS = ("TIMEOUT", "TIMED OUT")
_QUOTA_MARKERS = ("RESOURCE_EXHAUSTED", "QUOTA", "BILLING")
_RETRYABLE_MARKERS = ("503", "UNAVAILABLE", "HIGH DEMAND", "OVERLOADED")

logger = logging.getLogger(__name__)


class AIProviderNotConfiguredError(Exception):
    """The AI provider key is missing so the feature cannot run."""


class AIProviderError(Exception):
    """The AI provider request failed (network, rate limit, bad key...)."""


class AIProviderBusyError(AIProviderError):
    """The provider is temporarily overloaded (retryable)."""


class AIProviderQuotaError(AIProviderBusyError):
    """The provider quota/billing limit is reached (not retryable)."""


class AIProviderTimeoutError(AIProviderError):
    """The provider did not answer in time."""


class InvalidAIResponseError(Exception):
    """The AI response was missing, malformed, or failed schema validation."""


def _classify_provider_error(exc: Exception) -> AIProviderError:
    """Translate a raw provider failure into a typed, actionable service error."""
    message = str(exc)
    logger.warning("Gemini request failed: %s", message)
    upper = message.upper()
    if any(marker in upper for marker in _TIMEOUT_MARKERS):
        return AIProviderTimeoutError(f"Gemini request timed out: {message}")
    if any(marker in upper for marker in _QUOTA_MARKERS):
        return AIProviderQuotaError(f"Gemini quota or billing limit reached: {message}")
    if any(marker in upper for marker in _RETRYABLE_MARKERS):
        return AIProviderBusyError(f"Gemini is temporarily unavailable: {message}")
    return AIProviderError(f"Gemini request failed: {message}")


def build_parse_prompt(text: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    return (
        "You are a task-parsing assistant for a professional task-management application.\n"
        "\n"
        f"TODAY'S DATE (use this to resolve relative dates): {today}\n"
        "\n"
        "USER INPUT:\n"
        f'"{text}"\n'
        "\n"
        "TASK: Extract structured task information from the input and return ONLY ONE JSON object.\n"
        "\n"
        "EXTRACTION RULES:\n"
        '1. "extracted" contains facts the user explicitly stated:\n'
        "   - title: a concise task name derived from the input (never null unless clarification is required)\n"
        '   - due_date: resolve relative deadlines (e.g. "tomorrow", "EOD Friday", "by Friday") to an exact '
        "ISO 8601 datetime using TODAY'S DATE. If no time is given, use the end of that day (18:00) unless the "
        'input implies otherwise ("EOD" means 17:00). If a deadline cannot be determined reliably, leave it null.\n'
        "   - assignees: people the user explicitly named as doing or receiving the task (array of strings)\n"
        "   - subtasks: explicitly mentioned sub-steps (array of strings)\n"
        '   - effort_hours: an explicit duration only if the user stated one ("it will take 3 hours"); otherwise null\n'
        '   - context: useful background explicitly mentioned (project, document, meeting, reason, "the why")\n'
        '2. "inferred" contains your own reasonable suggestions, NOT facts:\n'
        '   - category: a short business category (e.g. "Reporting", "Finance", "Meeting Prep"); null if uncertain\n'
        "   - priority_score: a number between 0.0 and 1.0 representing urgency/importance; null if unclear\n"
        '3. "confidence_level": a number between 0.0 and 1.0 measuring extraction confidence.\n'
        "4. Do NOT invent anything the user did not say: no fake names, dates, projects, people, effort, or background.\n"
        '5. If important information is missing or ambiguous, do not guess: set "clarification.required" to true, '
        'give ONE concise "clarification.question", and list the unclear fields in "clarification.missing_fields".\n'
        "6. Assignees and subtasks must be JSON arrays of strings (use [] if none). All keys must always be present.\n"
        "7. Return ONLY the JSON object. No markdown fences, no commentary, no prose.\n"
        "\n"
        "JSON response format:\n"
        "{\n"
        '  "extracted": {\n'
        '    "title": string or null,\n'
        '    "due_date": ISO 8601 string or null,\n'
        '    "assignees": [string],\n'
        '    "subtasks": [string],\n'
        '    "effort_hours": number or null,\n'
        '    "context": string or null\n'
        "  },\n"
        '  "inferred": {\n'
        '    "category": string or null,\n'
        '    "priority_score": number or null\n'
        "  },\n"
        '  "confidence_level": number or null,\n'
        '  "clarification": {\n'
        '    "required": boolean,\n'
        '    "question": string or null,\n'
        '    "missing_fields": [string]\n'
        "  }\n"
        "}\n"
    )
def _strip_code_fences(raw: str) -> str:
    """Remove a markdown code fence if the model wrapped the JSON in one."""
    lines = raw.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
def build_categorize_prompt(title: str, description: str | None, context: str | None) -> str:
    """Prompt for explainable task categorization (Rules section 10)."""
    return (
        "You are a task-categorization assistant. Classify the following task.\n"
        "\n"
        "TASK:\n"
        f"title: {title}\n"
        f"description: {description or ''}\n"
        f"context: {context or ''}\n"
        "\n"
        "RULES:\n"
        "- Only use information present in the task; do NOT invent projects, people, or skills.\n"
        "- Provide a one-line reason for each suggestion under 'reasons' (empty string when null).\n"
        "- urgency must be exactly 'low', 'medium', or 'high', or null.\n"
        "- confidence_level is a number between 0.0 and 1.0.\n"
        "- Return ONLY one JSON object. No markdown, no commentary:\n"
        '{"suggested": {"category": str|null, "project": str|null, "area_of_focus": str|null, '
        '"urgency": "low"|"medium"|"high"|null, "skills_required": [str]}, '
        '"reasons": {"category": str, "project": str, "area_of_focus": str, "urgency": str, '
        '"skills_required": str}, "confidence_level": number|null}\n'
    )


def _task_line(task) -> str:
    due = task.due_date.isoformat() if task.due_date else "none"
    effort = task.effort_hours if task.effort_hours is not None else "unknown"
    return (
        f"- id={task.id} | {task.title} | due={due} | "
        f"effort_h={effort} | urgency={task.urgency or 'unknown'}"
    )


def build_prioritize_prompt(tasks) -> str:
    """Prompt for explainable task ranking (Rules section 10)."""
    today = datetime.now().strftime("%Y-%m-%d")
    task_lines = "\n".join(_task_line(task) for task in tasks)
    return (
        "You are a task-prioritization assistant. Rank the open tasks below.\n"
        "\n"
        f"TODAY'S DATE: {today}\n"
        "\n"
        "OPEN TASKS:\n"
        f"{task_lines}\n"
        "\n"
        "RULES:\n"
        "- Score every listed task exactly once, using only the listed ids.\n"
        "- priority_score, impact_score, feasibility_score are numbers 0.0-1.0 (null if unknown).\n"
        "- Sooner deadlines, higher urgency/impact and smaller effort usually rank higher.\n"
        "- Do NOT invent tasks, ids, deadlines, effort or people.\n"
        "- Give a one-line reason per task.\n"
        "- suggestions: at most 3 short actions (defer / reschedule / reprioritize); [] if none.\n"
        "- confidence_level is a number between 0.0 and 1.0.\n"
        "- Return ONLY one JSON object. No markdown, no commentary:\n"
        '{"items": [{"task_id": int, "priority_score": number, "impact_score": number|null, '
        '"feasibility_score": number|null, "reason": string}], "suggestions": [string], '
        '"confidence_level": number|null}\n'
    )


def build_breakdown_prompt(title: str, description: str | None, context: str | None) -> str:
    """Prompt for step-by-step task decomposition (Rules section 10)."""
    return (
        "You are a task-decomposition assistant. Break the task below into smaller steps.\n"
        "\n"
        "TASK:\n"
        f"title: {title}\n"
        f"description: {description or ''}\n"
        f"context: {context or ''}\n"
        "\n"
        "RULES:\n"
        "- Produce 3 to 8 ordered, concrete, actionable steps.\n"
        "- Each step is a short imperative phrase (no numbering, no extra text).\n"
        "- Do NOT invent people, dates, tools or facts that are not in the task.\n"
        "- If the task lacks enough detail, return fewer steps rather than inventing information.\n"
        "- confidence_level is a number between 0.0 and 1.0.\n"
        "- Return ONLY one JSON object. No markdown, no commentary:\n"
        '{"subtasks": [string], "confidence_level": number|null}\n'
    )


def build_next_action_prompt(
    title: str,
    description: str | None,
    context: str | None,
    subtasks: list[str] | None,
) -> str:
    """Prompt for Priority 5 (Suggested Next Action) — Rules section 5.5 and 10."""
    subtasks_text = ", ".join(subtasks) if subtasks else "none"
    return (
        "You are an actionable task assistant. Identify a small number of immediately actionable next steps.\n"
        "\n"
        "TASK:\n"
        f"title: {title}\n"
        f"description: {description or ''}\n"
        f"context: {context or ''}\n"
        f"existing_subtasks: {subtasks_text}\n"
        "\n"
        "RULES (Rules section 5.5):\n"
        "- Return 1 to 3 immediately actionable next steps (prefer a small number rather than overwhelming).\n"
        "- Each action must be a clear, practical, standalone imperative phrase.\n"
        "- If existing subtasks are present, suggest the most logical next step to begin or unblock work.\n"
        "- Do NOT invent people, tools, dates, or details absent from the task.\n"
        "- confidence_level is a number between 0.0 and 1.0.\n"
        "- Return ONLY one JSON object. No markdown, no commentary:\n"
        '{"actions": [string], "confidence_level": number|null}\n'
    )


def _analyze_workload(tasks, available_hours: float | None) -> schemas.WorkloadAnalysis:
    """Deterministic capacity check; never delegated to the AI."""
    estimates = [task.effort_hours for task in tasks if task.effort_hours is not None]
    required = round(sum(estimates), 2)
    if available_hours is None:
        return schemas.WorkloadAnalysis(
            required_hours=required,
            message="No capacity provided; workload cannot be compared against available time.",
        )

    fits = None if len(estimates) < len(tasks) else required <= available_hours
    if fits is None:
        state = f"capacity unknown ({len(tasks) - len(estimates)} task(s) without an estimate)"
    elif fits:
        state = "workload fits"
    else:
        state = "unrealistic workload; consider deferring, rescheduling or reprioritizing"
    return schemas.WorkloadAnalysis(
        available_hours=available_hours,
        required_hours=required,
        fits=fits,
        message=f"{required}h required vs {available_hours}h available: {state}.",
    )


def _validate_task_ids(result: schemas.TaskPrioritizeResult, tasks) -> None:
    """Reject AI output referencing tasks that were not submitted (Rule 8)."""
    known = {task.id for task in tasks}
    unknown = sorted({item.task_id for item in result.items} - known)
    if unknown:
        raise InvalidAIResponseError(f"AI returned unknown task ids: {unknown}")


_LEVEL_ORDER = ["info", "warning", "urgent"]


def _escalate(level: str) -> str:
    index = min(_LEVEL_ORDER.index(level) + 1, len(_LEVEL_ORDER) - 1)
    return _LEVEL_ORDER[index]


def build_reminders(tasks, today: datetime | None = None) -> list[schemas.Reminder]:
    """Deterministic reminders for open tasks due within 3 days (Rules section 5.6).

    Escalates when an urgent, imminent task shows no sign of starting
    (example: due tomorrow + not started -> increase reminder urgency).
    "Not started" is inferred from the task having no subtasks yet - the only
    start signal the current data model holds.
    """
    today = (today or datetime.now()).date()
    reminders = []
    for task in tasks:
        if task.completed or not task.due_date:
            continue
        days_left = (task.due_date.date() - today).days
        if days_left > 3:
            continue

        if days_left < 0:
            level, message = "urgent", f"Overdue by {-days_left} day(s)."
        elif days_left == 0:
            level, message = "urgent", "Due today."
        elif days_left == 1:
            level, message = "warning", "Due tomorrow."
        else:
            level, message = "info", f"Due in {days_left} days."

        not_started = not task.subtasks
        if days_left <= 1 and not_started:
            level = _escalate(level)
            message += " Not started (no subtasks yet) - escalated."
        if task.urgency == "high":
            level = _escalate(level)
            message += " Marked high urgency."
        if task.context:
            message += f" Context: {task.context}"

        reminders.append(schemas.Reminder(
            task_id=task.id,
            title=task.title,
            due_date=task.due_date,
            level=level,
            message=message,
        ))

    reminders.sort(key=lambda reminder: (-_LEVEL_ORDER.index(reminder.level), reminder.due_date))
    return reminders


# P3 (Rules section 7): minimum completed tasks before any learned estimate may be
# claimed. Below this, Priority 7 output MUST say "insufficient_data", never a pattern.
MIN_HISTORY_FOR_LEARNING = 5


def learning_confidence(completed_tasks: list) -> str:
    """History gate for future Priority 7 output (Rules section 7)."""
    if len(completed_tasks) < MIN_HISTORY_FOR_LEARNING:
        return "insufficient_data"
    return "ok"


def _normalize_result(result: schemas.TaskParseResult) -> schemas.TaskParseResult:
    """Normalize None arrays to empty lists for a consistent API response."""
    data = result.model_dump()
    for key in ("assignees", "subtasks"):
        if not data["extracted"].get(key):
            data["extracted"][key] = []
    return schemas.TaskParseResult.model_validate(data)


class TaskParsingService:
    def __init__(self, client=None, model: str | None = None):
        self._client = client
        self._model = model or DEFAULT_GEMINI_MODEL

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise AIProviderNotConfiguredError(
                "GEMINI_API_KEY is not set; AI task parsing is not configured."
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - SDK is a declared dependency
            raise AIProviderNotConfiguredError(
                "google-genai SDK is not installed (pip install google-genai)."
            ) from exc
        # Bounded request so a hung provider cannot block the endpoint indefinitely.
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=_GEMINI_TIMEOUT_MS),
        )

    def _generate_json(self, text: str) -> dict:
        """Call the provider, retrying transient overload/rate-limit failures."""
        client = self._get_client()
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._request_json(client, text)
            except AIProviderQuotaError:
                raise  # quota/billing limits do not clear in seconds
            except AIProviderBusyError:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
                delay = _RETRY_DELAY_SECONDS * (attempt + 1)
                logger.warning(
                    "Gemini busy; retrying in %.1fs (attempt %d/%d)",
                    delay, attempt + 1, _MAX_ATTEMPTS,
                )
                time.sleep(delay)

    def _request_json(self, client, text: str) -> dict:
        """One provider attempt. Provider failures are classified, never swallowed."""
        try:
            from google.genai import types

            response = client.models.generate_content(
                model=self._model,
                contents=text,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
        except AIProviderNotConfiguredError:
            raise
        except Exception as exc:
            raise _classify_provider_error(exc) from exc

        raw = (response.text or "").strip()
        if not raw:
            raise InvalidAIResponseError("AI provider returned an empty response.")

        try:
            return json.loads(_strip_code_fences(raw))
        except json.JSONDecodeError as exc:
            raise InvalidAIResponseError(f"AI response is not valid JSON: {exc}") from exc

    @staticmethod
    def _validate(model_cls, data):
        """Parse and validate AI output; reject gracefully when invalid (Rule 8)."""
        try:
            return model_cls.model_validate(data)
        except Exception as exc:
            raise InvalidAIResponseError(
                f"AI response failed schema validation: {exc}"
            ) from exc

    def parse(self, text: str) -> schemas.TaskParseResult:
        data = self._generate_json(build_parse_prompt(text))
        # `text` echoes the user's input; it is an application field, not AI output.
        data["text"] = text
        return _normalize_result(self._validate(schemas.TaskParseResult, data))

    def categorize(
        self,
        task_id: int,
        title: str,
        description: str | None,
        context: str | None,
    ) -> schemas.TaskCategorizationResult:
        data = self._generate_json(build_categorize_prompt(title, description, context))
        data["task_id"] = task_id
        return self._validate(schemas.TaskCategorizationResult, data)

    def prioritize(self, tasks, available_hours: float | None) -> schemas.TaskPrioritizeResult:
        data = self._generate_json(build_prioritize_prompt(tasks))
        # Workload is deterministic application logic, not an AI calculation.
        data["workload"] = _analyze_workload(tasks, available_hours).model_dump()

        result = self._validate(schemas.TaskPrioritizeResult, data)
        _validate_task_ids(result, tasks)
        result.items.sort(key=lambda item: item.priority_score, reverse=True)
        return result

    def breakdown(
        self,
        task_id: int,
        title: str,
        description: str | None,
        context: str | None,
    ) -> schemas.TaskBreakdownResult:
        data = self._generate_json(build_breakdown_prompt(title, description, context))
        data["task_id"] = task_id
        return self._validate(schemas.TaskBreakdownResult, data)

    def next_action(
        self,
        task_id: int,
        title: str,
        description: str | None,
        context: str | None,
        subtasks: list[str] | None,
    ) -> schemas.TaskNextActionResult:
        data = self._generate_json(build_next_action_prompt(title, description, context, subtasks))
        data["task_id"] = task_id
        return self._validate(schemas.TaskNextActionResult, data)


def get_task_parsing_service() -> TaskParsingService:
    return TaskParsingService()