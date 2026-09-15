import json
import os
from datetime import datetime

from . import schemas

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


class AIProviderNotConfiguredError(Exception):
    """The AI provider key is missing so the feature cannot run."""


class AIProviderError(Exception):
    """The AI provider request failed (network, timeout, rate limit, bad key...)."""


class InvalidAIResponseError(Exception):
    """The AI response was missing, malformed, or failed schema validation."""


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
        except ImportError as exc:  # pragma: no cover - SDK is a declared dependency
            raise AIProviderNotConfiguredError(
                "google-genai SDK is not installed (pip install google-genai)."
            ) from exc
        return genai.Client(api_key=api_key)

    def parse(self, text: str) -> schemas.TaskParseResult:
        client = self._get_client()
        prompt = build_parse_prompt(text)

        try:
            from google.genai import types

            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
        except AIProviderNotConfiguredError:
            raise
        except Exception as exc:
            raise AIProviderError(f"Gemini request failed: {exc}") from exc

        raw = (response.text or "").strip()
        if not raw:
            raise InvalidAIResponseError("AI provider returned an empty response.")

        raw = _strip_code_fences(raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise InvalidAIResponseError(f"AI response is not valid JSON: {exc}") from exc

        # `text` echoes the user's input; it is an application field, not AI output.
        data["text"] = text

        try:
            result = schemas.TaskParseResult.model_validate(data)
        except Exception as exc:
            raise InvalidAIResponseError(
                f"AI response failed schema validation: {exc}"
            ) from exc

        return _normalize_result(result)


def get_task_parsing_service() -> TaskParsingService:
    return TaskParsingService()