"""Tests for Phase 1 - Smart Task Input.

Covers the scenarios required by Rules.md section 19 for an AI feature:
normal / minimal / ambiguous / complex input, invalid AI response, AI
unavailable, and existing-task (CRUD) regression.

The Gemini provider is never called during tests: AI behaviour is either
injected through canned structured output or exercised through the service's
error paths (provider not configured / provider failure / invalid response).
"""

import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app import models, schemas, services


class FakeParsingService:
    """Stand-in for TaskParsingService used by endpoint-level tests."""

    def __init__(
        self,
        result=None,
        error=None,
        categorize_result=None,
        categorize_error=None,
        prioritize_result=None,
        prioritize_error=None,
        breakdown_result=None,
        breakdown_error=None,
        next_action_result=None,
        next_action_error=None,
    ):
        self.result = result
        self.error = error
        self.categorize_result = categorize_result
        self.categorize_error = categorize_error
        self.prioritize_result = prioritize_result
        self.prioritize_error = prioritize_error
        self.breakdown_result = breakdown_result
        self.breakdown_error = breakdown_error
        self.next_action_result = next_action_result
        self.next_action_error = next_action_error

    def parse(self, text):
        if self.error is not None:
            raise self.error
        return self.result

    def categorize(self, task_id, title, description, context):
        if self.categorize_error is not None:
            raise self.categorize_error
        return self.categorize_result

    def prioritize(self, tasks, available_hours):
        if self.prioritize_error is not None:
            raise self.prioritize_error
        return self.prioritize_result

    def breakdown(self, task_id, title, description, context):
        if self.breakdown_error is not None:
            raise self.breakdown_error
        return self.breakdown_result

    def next_action(self, task_id, title, description, context, subtasks):
        if self.next_action_error is not None:
            raise self.next_action_error
        return self.next_action_result


def make_categorization(**overrides):
    data = {
        "task_id": 1,
        "suggested": {
            "category": "Reporting",
            "project": "Q4 Finance",
            "area_of_focus": "Reporting",
            "urgency": "high",
            "skills_required": ["analysis"],
        },
        "reasons": {
            "category": "task is the Q4 reporting effort",
            "project": "explicitly mentions Q4 work",
            "area_of_focus": "it is a reporting task",
            "urgency": "deadline-driven deliverable",
            "skills_required": "needs analysis",
        },
        "confidence_level": 0.9,
    }
    data.update(overrides)
    return schemas.TaskCategorizationResult.model_validate(data)


def make_prioritization(**overrides):
    data = {
        "items": [
            {
                "task_id": 1,
                "priority_score": 0.8,
                "impact_score": 0.7,
                "feasibility_score": 0.6,
                "reason": "deadline soon",
            }
        ],
        "workload": {
            "available_hours": 4.0,
            "required_hours": 2.0,
            "fits": True,
            "message": "Workload fits.",
        },
        "suggestions": ["Start with the top item"],
        "confidence_level": 0.8,
    }
    data.update(overrides)
    return schemas.TaskPrioritizeResult.model_validate(data)


def make_todo(task_id, title="task", effort_hours=None, due_date=None, urgency=None):
    return models.Todo(
        id=task_id,
        title=title,
        effort_hours=effort_hours,
        due_date=due_date,
        urgency=urgency,
    )


def make_breakdown(**overrides):
    data = {
        "task_id": 1,
        "subtasks": ["Gather data", "Analyse results", "Draft report"],
        "confidence_level": 0.8,
    }
    data.update(overrides)
    return schemas.TaskBreakdownResult.model_validate(data)


def make_next_action(**overrides):
    data = {
        "task_id": 1,
        "actions": ["Gather Q4 data", "Draft the summary"],
        "confidence_level": 0.85,
    }
    data.update(overrides)
    return schemas.TaskNextActionResult.model_validate(data)


def make_result(**overrides):
    data = {
        "text": "Finish the Q4 report by Friday and send it to Sarah.",
        "extracted": {
            "title": "Finish the Q4 report",
            "due_date": "2026-09-18T17:00:00",
            "assignees": ["Sarah"],
            "subtasks": [],
            "effort_hours": 3.0,
            "context": "Q4 reporting",
        },
        "inferred": {"category": "Reporting", "priority_score": 0.8},
        "confidence_level": 0.9,
        "clarification": {"required": False, "question": None, "missing_fields": []},
    }
    data.update(overrides)
    return schemas.TaskParseResult.model_validate(data)


# --- Baseline and existing CRUD regression ----------------------------


def test_root_ok(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "TODO API is running"}


def test_ui_is_served(client):
    response = client.get("/ui/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "TODO AI" in response.text


def test_ui_assets_are_served(client):
    for path, marker in (("/ui/app.js", "javascript"), ("/ui/styles.css", "css")):
        response = client.get(path)
        assert response.status_code == 200
        assert marker in response.headers["content-type"]


def test_crud_regression_with_ai_metadata(client):
    payload = {
        "title": "Write tests",
        "description": "cover smart input",
        "due_date": "2026-09-20T12:00:00",
        "assignees": ["me"],
        "subtasks": ["draft", "review"],
        "context": "phase 1",
        "effort_hours": 2.5,
        "category": "Engineering",
        "project": "Prep",
        "area_of_focus": "Quality",
        "skills_required": ["python", "testing"],
        "urgency": "medium",
        "priority_score": 0.7,
        "impact_score": 0.6,
        "feasibility_score": 0.5,
        "confidence_level": 0.8,
        "created_from": "natural_language",
    }
    created = client.post("/todos/", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Write tests"
    assert body["due_date"] == "2026-09-20T12:00:00"
    assert body["assignees"] == ["me"]
    assert body["subtasks"] == ["draft", "review"]
    assert body["category"] == "Engineering"
    assert body["project"] == "Prep"
    assert body["area_of_focus"] == "Quality"
    assert body["skills_required"] == ["python", "testing"]
    assert body["urgency"] == "medium"
    assert body["priority_score"] == 0.7
    assert body["impact_score"] == 0.6
    assert body["feasibility_score"] == 0.5
    assert body["created_from"] == "natural_language"

    todo_id = body["id"]
    assert client.get(f"/todos/{todo_id}").json()["subtasks"] == ["draft", "review"]

    updated = client.put(
        f"/todos/{todo_id}",
        json={"title": "Write more tests", "description": "still", "completed": True},
    )
    assert updated.status_code == 200
    updated_body = updated.json()
    assert updated_body["completed"] is True
    # partial update: metadata not present in the body must be preserved
    assert updated_body["title"] == "Write more tests"
    assert updated_body["assignees"] == ["me"]
    assert updated_body["subtasks"] == ["draft", "review"]
    assert updated_body["due_date"] == "2026-09-20T12:00:00"
    assert updated_body["category"] == "Engineering"
    assert updated_body["project"] == "Prep"
    assert updated_body["area_of_focus"] == "Quality"
    assert updated_body["skills_required"] == ["python", "testing"]
    assert updated_body["urgency"] == "medium"
    assert updated_body["priority_score"] == 0.7
    assert updated_body["impact_score"] == 0.6
    assert updated_body["feasibility_score"] == 0.5

    assert client.get("/todos/").status_code == 200

    assert client.delete(f"/todos/{todo_id}").status_code == 204
    assert client.get(f"/todos/{todo_id}").status_code == 404


def test_crud_minimal_body_unchanged(client):
    created = client.post("/todos/", json={"title": "plain task"})
    assert created.status_code == 201
    assert created.json()["completed"] is False
    assert created.json()["created_from"] == "ui"
    todo_id = created.json()["id"]
    assert client.delete(f"/todos/{todo_id}").status_code == 204
# --- POST /api/tasks/parse ---------------------------------------------

def test_put_partial_update_preserves_unspecified_fields(client):
    created = client.post(
        "/todos/",
        json={
            "title": "initial",
            "due_date": "2026-09-20T12:00:00",
            "assignees": ["Sarah"],
            "subtasks": ["a", "b"],
            "category": "Finance",
            "project": "Q4",
            "area_of_focus": "Reporting",
            "skills_required": ["analysis"],
            "urgency": "low",
            "priority_score": 0.8,
            "impact_score": 0.6,
            "feasibility_score": 0.5,
            "confidence_level": 0.7,
            "effort_hours": 2.0,
            "context": "ctx",
        },
    )
    assert created.status_code == 201
    todo_id = created.json()["id"]

    renamed = client.put(f"/todos/{todo_id}", json={"title": "renamed"})
    assert renamed.status_code == 200
    body = renamed.json()
    assert body["title"] == "renamed"
    assert body["assignees"] == ["Sarah"]
    assert body["subtasks"] == ["a", "b"]
    assert body["due_date"] == "2026-09-20T12:00:00"
    assert body["category"] == "Finance"
    assert body["project"] == "Q4"
    assert body["area_of_focus"] == "Reporting"
    assert body["skills_required"] == ["analysis"]
    assert body["urgency"] == "low"
    assert body["priority_score"] == 0.8
    assert body["impact_score"] == 0.6
    assert body["feasibility_score"] == 0.5
    assert body["confidence_level"] == 0.7
    assert body["effort_hours"] == 2.0
    assert body["context"] == "ctx"

    partial = client.put(
        f"/todos/{todo_id}", json={"priority_score": 0.9, "category": "Reporting"}
    )
    assert partial.status_code == 200
    p = partial.json()
    assert p["title"] == "renamed"
    assert p["category"] == "Reporting"
    assert p["priority_score"] == 0.9
    assert p["assignees"] == ["Sarah"]

    toggled = client.put(f"/todos/{todo_id}", json={"completed": True})
    assert toggled.status_code == 200
    assert toggled.json()["completed"] is True
    assert toggled.json()["title"] == "renamed"

    assert client.delete(f"/todos/{todo_id}").status_code == 204


def test_put_explicit_null_clears_field(client):
    created = client.post(
        "/todos/", json={"title": "hi", "assignees": ["Sarah"], "subtasks": ["a"]}
    ).json()
    todo_id = created["id"]
    cleared = client.put(f"/todos/{todo_id}", json={"assignees": None})
    assert cleared.status_code == 200
    assert cleared.json()["assignees"] is None
    assert cleared.json()["subtasks"] == ["a"]
    assert cleared.json()["title"] == "hi"
    assert client.delete(f"/todos/{todo_id}").status_code == 204


def test_put_empty_body_rejected(client):
    created = client.post("/todos/", json={"title": "hi"}).json()
    todo_id = created["id"]
    assert client.put(f"/todos/{todo_id}", json={}).status_code == 400
    assert client.delete(f"/todos/{todo_id}").status_code == 204

def test_parse_endpoint_returns_structured_suggestion(client):
    result = make_result()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        result=result
    )
    response = client.post("/api/tasks/parse", json={"text": result.text})
    assert response.status_code == 200
    body = response.json()
    assert body["extracted"]["title"] == "Finish the Q4 report"
    assert body["extracted"]["assignees"] == ["Sarah"]
    assert body["extracted"]["effort_hours"] == 3.0
    assert body["inferred"]["category"] == "Reporting"
    assert body["inferred"]["priority_score"] == 0.8
    assert body["clarification"]["required"] is False


def test_parse_ambiguous_input_returns_clarification(client):
    result = make_result(
        text="Do the presentation Friday.",
        extracted={
            "title": "Do the presentation",
            "due_date": None,
            "assignees": [],
            "subtasks": [],
            "effort_hours": None,
            "context": None,
        },
        inferred={"category": None, "priority_score": None},
        confidence_level=0.5,
        clarification={
            "required": True,
            "question": "What time on Friday should the presentation be scheduled?",
            "missing_fields": ["due_date"],
        },
    )
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        result=result
    )
    response = client.post("/api/tasks/parse", json={"text": "Do the presentation Friday."})
    assert response.status_code == 200
    body = response.json()
    assert body["clarification"]["required"] is True
    assert body["clarification"]["question"] is not None
    assert body["extracted"]["due_date"] is None


def test_parse_not_configured_returns_503(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post("/api/tasks/parse", json={"text": "Finish the report."})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_parse_provider_failure_returns_502(client):
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        error=services.AIProviderError("boom")
    )
    response = client.post("/api/tasks/parse", json={"text": "Finish the report."})
    assert response.status_code == 502


def test_parse_invalid_ai_response_returns_502(client):
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        error=services.InvalidAIResponseError("bad json")
    )
    response = client.post("/api/tasks/parse", json={"text": "Finish the report."})
    assert response.status_code == 502


def test_parse_empty_text_rejected(client):
    assert client.post("/api/tasks/parse", json={"text": ""}).status_code == 422


def test_parse_overlong_text_rejected(client):
    assert client.post("/api/tasks/parse", json={"text": "x" * 2001}).status_code == 422


def test_parse_whitespace_only_rejected(client):
    assert client.post("/api/tasks/parse", json={"text": "   "}).status_code == 422


def test_parse_strips_surrounding_whitespace_before_service(client):
    captured = {}

    def factory():
        class Recording(FakeParsingService):
            def parse(self, text):
                captured["text"] = text
                return make_result(text=text)

        return Recording()

    app.dependency_overrides[services.get_task_parsing_service] = factory
    response = client.post("/api/tasks/parse", json={"text": "  Finish the report.  "})
    assert response.status_code == 200
    assert captured["text"] == "Finish the report."


def test_task_parse_request_strips_text():
    assert schemas.TaskParseRequest(text="  hello  ").text == "hello"
# --- Service-level: real parse() flow with a fake Gemini client ---------


class FakeGeminiResponse:
    def __init__(self, text):
        self.text = text


class FakeGeminiClient:
    class _Models:
        def __init__(self, response, error, errors):
            self._response = response
            self._error = error
            self._errors = list(errors or [])
            self.calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            if self._errors:  # transient failures raised first, in order
                raise self._errors.pop(0)
            if self._error is not None:
                raise self._error
            return self._response

    def __init__(self, response=None, error=None, errors=None):
        self.models = self._Models(response, error, errors)


def test_service_parse_happy_path_and_normalizes_null_arrays():
    raw = (
        '```json\n'
        '{\n'
        '  "extracted": {\n'
        '    "title": "Finish the Q4 report",\n'
        '    "due_date": "2026-09-18T17:00:00",\n'
        '    "assignees": null,\n'
        '    "subtasks": null,\n'
        '    "effort_hours": 3,\n'
        '    "context": "Q4 reporting"\n'
        '  },\n'
        '  "inferred": {"category": "Reporting", "priority_score": 0.8},\n'
        '  "confidence_level": 0.9,\n'
        '  "clarification": {"required": false, "question": null, "missing_fields": []}\n'
        '}\n'
        '```'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))
    result = service.parse("Finish the Q4 report by Friday and send it to Sarah.")
    assert result.extracted.title == "Finish the Q4 report"
    assert result.extracted.assignees == []
    assert result.extracted.subtasks == []
    assert result.extracted.effort_hours == 3.0
    assert result.inferred.priority_score == 0.8
    assert result.clarification.required is False


def test_service_parse_rejects_malformed_json():
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse("not json")))
    with pytest.raises(services.InvalidAIResponseError):
        service.parse("Finish the report.")


def test_service_parse_rejects_empty_response():
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(None)))
    with pytest.raises(services.InvalidAIResponseError):
        service.parse("Finish the report.")


def test_service_parse_rejects_out_of_range_values():
    raw = (
        '{"extracted": {"title": "x", "due_date": null, "assignees": [], "subtasks": [], '
        '"effort_hours": -1, "context": null}, '
        '"inferred": {"category": null, "priority_score": null}, '
        '"confidence_level": null, '
        '"clarification": {"required": false, "question": null, "missing_fields": []}}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))
    with pytest.raises(services.InvalidAIResponseError):
        service.parse("x")


def test_service_parse_wraps_provider_errors():
    service = services.TaskParsingService(client=FakeGeminiClient(error=RuntimeError("network down")))
    with pytest.raises(services.AIProviderError):
        service.parse("Finish the report.")


def test_service_not_configured_without_env_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    service = services.TaskParsingService()
    with pytest.raises(services.AIProviderNotConfiguredError):
        service.parse("Finish the report.")


# --- Transient provider failures (Google "503 UNAVAILABLE ... high demand") --

SIMPLE_RAW = (
    '{"extracted": {"title": "Finish report", "due_date": null, "assignees": [], "subtasks": [], '
    '"effort_hours": null, "context": null}, "inferred": {"category": null, "priority_score": null}, '
    '"confidence_level": null, "clarification": {"required": false, "question": null, "missing_fields": []}}'
)
HIGH_DEMAND = "503 UNAVAILABLE. This model is currently experiencing high demand. Please try again later."


def test_service_retries_transient_overload_then_succeeds(monkeypatch):
    monkeypatch.setattr(services, "_RETRY_DELAY_SECONDS", 0)
    client = FakeGeminiClient(
        response=FakeGeminiResponse(SIMPLE_RAW),
        errors=[RuntimeError(HIGH_DEMAND), RuntimeError(HIGH_DEMAND)],
    )
    result = services.TaskParsingService(client=client).parse("Finish report.")
    assert result.extracted.title == "Finish report"
    assert client.models.calls == 3  # two retries, then success


def test_service_raises_busy_error_when_overload_persists(monkeypatch):
    monkeypatch.setattr(services, "_RETRY_DELAY_SECONDS", 0)
    client = FakeGeminiClient(error=RuntimeError(HIGH_DEMAND))
    with pytest.raises(services.AIProviderBusyError):
        services.TaskParsingService(client=client).parse("Finish report.")
    assert client.models.calls == services._MAX_ATTEMPTS


def test_service_classifies_timeout_as_timeout_error():
    client = FakeGeminiClient(error=TimeoutError("request timed out"))
    with pytest.raises(services.AIProviderTimeoutError):
        services.TaskParsingService(client=client).parse("Finish report.")


QUOTA_MESSAGE = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, "
    "please check your plan and billing details.'}}"
)


def test_service_does_not_retry_quota_exhaustion(monkeypatch):
    monkeypatch.setattr(services, "_RETRY_DELAY_SECONDS", 0)
    client = FakeGeminiClient(error=RuntimeError(QUOTA_MESSAGE))
    with pytest.raises(services.AIProviderQuotaError):
        services.TaskParsingService(client=client).parse("Finish report.")
    assert client.models.calls == 1  # retrying a quota limit is pointless


def test_parse_provider_quota_returns_429_with_hint(client):
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        error=services.AIProviderQuotaError("quota reached")
    )
    response = client.post("/api/tasks/parse", json={"text": "Finish the report."})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert "quota" in response.json()["detail"].lower()


def test_service_does_not_retry_non_transient_errors():
    client = FakeGeminiClient(error=RuntimeError("API key not valid"))
    with pytest.raises(services.AIProviderError) as raised:
        services.TaskParsingService(client=client).parse("Finish report.")
    assert not isinstance(raised.value, services.AIProviderBusyError)
    assert client.models.calls == 1


def test_parse_provider_busy_returns_429_with_retry_after(client):
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        error=services.AIProviderBusyError("high demand")
    )
    response = client.post("/api/tasks/parse", json={"text": "Finish the report."})
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert "busy" in response.json()["detail"].lower()


def test_parse_provider_timeout_returns_504(client):
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        error=services.AIProviderTimeoutError("timed out")
    )
    response = client.post("/api/tasks/parse", json={"text": "Finish the report."})
    assert response.status_code == 504


def test_categorize_provider_busy_returns_429(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        categorize_error=services.AIProviderBusyError("high demand")
    )
    response = client.post("/api/tasks/categorize", json={"task_id": created["id"]})
    assert response.status_code == 429
    client.delete(f"/todos/{created['id']}")


# --- AI output validation (Rule 8: never trust AI output) ------------------


def test_ai_response_validation_rejects_negative_effort():
    with pytest.raises(ValidationError):
        make_result(
            extracted={
                "title": "x",
                "due_date": None,
                "assignees": [],
                "subtasks": [],
                "effort_hours": -1.0,
                "context": None,
            }
        )


def test_ai_response_validation_rejects_priority_out_of_range():
    with pytest.raises(ValidationError):
        make_result(inferred={"category": None, "priority_score": 1.5})


def test_ai_response_validation_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        make_result(confidence_level=1.4)


def test_ai_response_validation_rejects_invalid_due_date():
    with pytest.raises(ValidationError):
        make_result(
            extracted={
                "title": "x",
                "due_date": "not-a-date",
                "assignees": [],
                "subtasks": [],
                "effort_hours": None,
                "context": None,
            }
        )


def test_strip_code_fences():
    assert services._strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert services._strip_code_fences('{"a": 1}') == '{"a": 1}'
# --- POST /api/tasks/categorize ------------------------------------------


def test_categorize_returns_suggestions_without_modifying_task(client):
    created = client.post("/todos/", json={"title": "Finish the Q4 report for Finance."}).json()
    todo_id = created["id"]

    result = make_categorization(task_id=todo_id)
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        categorize_result=result
    )
    response = client.post("/api/tasks/categorize", json={"task_id": todo_id})
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == todo_id
    assert body["suggested"]["category"] == "Reporting"
    assert body["suggested"]["urgency"] == "high"
    assert body["suggested"]["skills_required"] == ["analysis"]
    assert body["reasons"]["category"]

    # Rule 7: the endpoint must NOT modify the task itself
    unchanged = client.get(f"/todos/{todo_id}").json()
    assert unchanged["category"] is None
    assert unchanged["project"] is None

    # client confirms and persists via the existing update flow
    applied = client.put(
        f"/todos/{todo_id}",
        json={
            "category": body["suggested"]["category"],
            "project": body["suggested"]["project"],
            "area_of_focus": body["suggested"]["area_of_focus"],
            "urgency": body["suggested"]["urgency"],
            "skills_required": body["suggested"]["skills_required"],
        },
    )
    assert applied.status_code == 200
    assert applied.json()["category"] == "Reporting"
    assert applied.json()["project"] == "Q4 Finance"
    assert applied.json()["urgency"] == "high"

    assert client.delete(f"/todos/{todo_id}").status_code == 204


def test_categorize_task_not_found_returns_404(client):
    assert client.post("/api/tasks/categorize", json={"task_id": 99999}).status_code == 404


def test_categorize_not_configured_returns_503(client, monkeypatch):
    created = client.post("/todos/", json={"title": "task"}).json()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post("/api/tasks/categorize", json={"task_id": created["id"]})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    client.delete(f"/todos/{created['id']}")


def test_categorize_provider_failure_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    todo_id = created["id"]
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        categorize_error=services.AIProviderError("boom")
    )
    response = client.post("/api/tasks/categorize", json={"task_id": todo_id})
    assert response.status_code == 502
    client.delete(f"/todos/{todo_id}")


def test_categorize_invalid_ai_response_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    todo_id = created["id"]
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        categorize_error=services.InvalidAIResponseError("bad json")
    )
    response = client.post("/api/tasks/categorize", json={"task_id": todo_id})
    assert response.status_code == 502
    client.delete(f"/todos/{todo_id}")


def test_service_categorize_happy_path():
    raw = (
        '{"suggested": {"category": "Reporting", "project": "Q4 Finance", '
        '"area_of_focus": "Reporting", "urgency": "high", '
        '"skills_required": ["analysis"]}, '
        '"reasons": {"category": "reporting work", "project": "mentions Q4", '
        '"area_of_focus": "reporting", "urgency": "deadline-driven", '
        '"skills_required": "analysis"}, '
        '"confidence_level": 0.9}'
    )
    service = services.TaskParsingService(
        client=FakeGeminiClient(response=FakeGeminiResponse(raw))
    )
    result = service.categorize(42, "Finish the Q4 report", "for Finance", "Q4")
    assert result.task_id == 42
    assert result.suggested.project == "Q4 Finance"
    assert result.suggested.urgency == "high"
    assert result.suggested.skills_required == ["analysis"]
    assert result.reasons["urgency"]
    assert result.confidence_level == 0.9


def test_categorization_suggestion_rejects_invalid_urgency():
    with pytest.raises(ValidationError):
        schemas.TaskCategorizationSuggestion.model_validate(
            {
                "category": None,
                "project": None,
                "area_of_focus": None,
                "urgency": "urgent",
                "skills_required": [],
            }
        )


def test_categorization_result_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        schemas.TaskCategorizationResult.model_validate(
            {
                "task_id": 1,
                "suggested": {
                    "category": None,
                    "project": None,
                    "area_of_focus": None,
                    "urgency": None,
                    "skills_required": [],
                },
                "reasons": {},
                "confidence_level": 1.5,
            }
        )

# --- POST /api/tasks/prioritize ------------------------------------------


def test_prioritize_returns_ranking_and_workload(client):
    first = client.post("/todos/", json={"title": "small", "effort_hours": 2.0}).json()
    second = client.post("/todos/", json={"title": "big", "effort_hours": 6.0}).json()
    result = make_prioritization(
        items=[
            {
                "task_id": second["id"],
                "priority_score": 0.9,
                "impact_score": 0.8,
                "feasibility_score": 0.5,
                "reason": "due soon",
            },
            {
                "task_id": first["id"],
                "priority_score": 0.4,
                "impact_score": None,
                "feasibility_score": None,
                "reason": "flexible",
            },
        ],
        workload={
            "available_hours": 4.0,
            "required_hours": 8.0,
            "fits": False,
            "message": "Unrealistic workload: 8h required vs 4h available.",
        },
    )
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        prioritize_result=result
    )
    response = client.post(
        "/api/tasks/prioritize",
        json={"task_ids": [first["id"], second["id"]], "available_hours": 4.0},
    )
    assert response.status_code == 200
    body = response.json()
    assert [item["task_id"] for item in body["items"]] == [second["id"], first["id"]]
    assert body["workload"]["fits"] is False
    assert body["suggestions"]

    # Rule 7: prioritization must not modify stored tasks
    assert client.get(f"/todos/{second['id']}").json()["priority_score"] is None

    client.delete(f"/todos/{first['id']}")
    client.delete(f"/todos/{second['id']}")


def test_prioritize_no_matching_tasks_returns_404(client):
    assert client.post("/api/tasks/prioritize", json={"task_ids": [99999]}).status_code == 404


def test_prioritize_not_configured_returns_503(client, monkeypatch):
    created = client.post("/todos/", json={"title": "task", "effort_hours": 1.0}).json()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post("/api/tasks/prioritize", json={"task_ids": [created["id"]]})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    client.delete(f"/todos/{created['id']}")


def test_prioritize_provider_failure_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        prioritize_error=services.AIProviderError("boom")
    )
    response = client.post("/api/tasks/prioritize", json={"task_ids": [created["id"]]})
    assert response.status_code == 502
    client.delete(f"/todos/{created['id']}")


def test_prioritize_invalid_ai_response_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        prioritize_error=services.InvalidAIResponseError("bad json")
    )
    response = client.post("/api/tasks/prioritize", json={"task_ids": [created["id"]]})
    assert response.status_code == 502
    client.delete(f"/todos/{created['id']}")

# --- Service-level: deterministic workload, sorting, id validation --------


def test_service_prioritize_computes_workload_and_sorts():
    raw = (
        '{"items": ['
        '{"task_id": 1, "priority_score": 0.3, "impact_score": null, '
        '"feasibility_score": null, "reason": "later"},'
        '{"task_id": 2, "priority_score": 0.9, "impact_score": 0.8, '
        '"feasibility_score": 0.5, "reason": "urgent"}],'
        '"suggestions": ["Defer task 1"], "confidence_level": 0.8}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))
    tasks = [make_todo(1, "later", effort_hours=2.0), make_todo(2, "urgent", effort_hours=3.0)]

    result = service.prioritize(tasks, available_hours=4.0)

    assert [item.task_id for item in result.items] == [2, 1]
    assert result.workload.required_hours == 5.0
    assert result.workload.fits is False
    assert "unrealistic workload" in result.workload.message


def test_service_prioritize_flags_missing_estimates():
    raw = (
        '{"items": [{"task_id": 1, "priority_score": 0.5, "impact_score": null, '
        '"feasibility_score": null, "reason": "x"}], '
        '"suggestions": [], "confidence_level": 0.5}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    result = service.prioritize([make_todo(1, "no estimate")], available_hours=4.0)

    assert result.workload.required_hours == 0.0
    assert result.workload.fits is None
    assert "unknown" in result.workload.message


def test_service_prioritize_without_capacity_skips_comparison():
    raw = (
        '{"items": [{"task_id": 1, "priority_score": 0.5, "impact_score": null, '
        '"feasibility_score": null, "reason": "x"}], '
        '"suggestions": [], "confidence_level": 0.5}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    result = service.prioritize([make_todo(1, "task", effort_hours=1.0)], available_hours=None)

    assert result.workload.available_hours is None
    assert result.workload.fits is None
    assert "No capacity provided" in result.workload.message


def test_service_prioritize_rejects_unknown_task_ids():
    raw = (
        '{"items": [{"task_id": 99, "priority_score": 0.5, "impact_score": null, '
        '"feasibility_score": null, "reason": "x"}], '
        '"suggestions": [], "confidence_level": 0.5}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    with pytest.raises(services.InvalidAIResponseError):
        service.prioritize([make_todo(1)], available_hours=None)


def test_service_prioritize_rejects_out_of_range_score():
    raw = (
        '{"items": [{"task_id": 1, "priority_score": 1.5, "impact_score": null, '
        '"feasibility_score": null, "reason": "x"}], '
        '"suggestions": [], "confidence_level": 0.5}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    with pytest.raises(services.InvalidAIResponseError):
        service.prioritize([make_todo(1)], available_hours=None)


def test_priority_score_schema_rejects_out_of_range():
    with pytest.raises(ValidationError):
        schemas.TaskPriorityScore.model_validate(
            {
                "task_id": 1,
                "priority_score": 1.5,
                "impact_score": None,
                "feasibility_score": None,
                "reason": None,
            }
        )

# --- POST /api/tasks/{task_id}/breakdown ---------------------------------


def test_breakdown_returns_subtasks_without_modifying_task(client):
    created = client.post(
        "/todos/", json={"title": "Write quarterly report", "context": "Q4"}
    ).json()
    todo_id = created["id"]
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        breakdown_result=make_breakdown(
            task_id=todo_id,
            subtasks=["Gather required data", "Analyse results", "Draft report"],
        )
    )
    response = client.post(f"/api/tasks/{todo_id}/breakdown")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == todo_id
    assert body["subtasks"] == ["Gather required data", "Analyse results", "Draft report"]

    # Rule 7: breakdown must not modify the task
    assert client.get(f"/todos/{todo_id}").json()["subtasks"] is None

    # client confirms and persists accepted steps via the existing update flow
    applied = client.put(f"/todos/{todo_id}", json={"subtasks": body["subtasks"]})
    assert applied.status_code == 200
    assert applied.json()["subtasks"] == body["subtasks"]
    assert applied.json()["title"] == "Write quarterly report"

    assert client.delete(f"/todos/{todo_id}").status_code == 204


def test_breakdown_task_not_found_returns_404(client):
    assert client.post("/api/tasks/99999/breakdown").status_code == 404


def test_breakdown_not_configured_returns_503(client, monkeypatch):
    created = client.post("/todos/", json={"title": "task"}).json()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post(f"/api/tasks/{created['id']}/breakdown")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    client.delete(f"/todos/{created['id']}")


def test_breakdown_provider_failure_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        breakdown_error=services.AIProviderError("boom")
    )
    response = client.post(f"/api/tasks/{created['id']}/breakdown")
    assert response.status_code == 502
    client.delete(f"/todos/{created['id']}")


def test_breakdown_invalid_ai_response_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        breakdown_error=services.InvalidAIResponseError("bad json")
    )
    response = client.post(f"/api/tasks/{created['id']}/breakdown")
    assert response.status_code == 502
    client.delete(f"/todos/{created['id']}")


# --- Service-level: breakdown parsing and validation ---------------------


def test_service_breakdown_happy_path():
    raw = (
        '{"subtasks": ["Gather data", " Analyse results ", "Draft report"], '
        '"confidence_level": 0.8}'
    )
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    result = service.breakdown(7, "Write quarterly report", None, None)

    assert result.task_id == 7
    assert result.subtasks == ["Gather data", "Analyse results", "Draft report"]
    assert result.confidence_level == 0.8


def test_service_breakdown_rejects_no_steps():
    raw = '{"subtasks": [], "confidence_level": 0.5}'
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    with pytest.raises(services.InvalidAIResponseError):
        service.breakdown(1, "task", None, None)


def test_service_breakdown_rejects_blank_steps():
    raw = '{"subtasks": ["  ", ""], "confidence_level": 0.5}'
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    with pytest.raises(services.InvalidAIResponseError):
        service.breakdown(1, "task", None, None)


def test_breakdown_result_rejects_too_many_steps():
    with pytest.raises(ValidationError):
        schemas.TaskBreakdownResult.model_validate(
            {"task_id": 1, "subtasks": [f"step {i}" for i in range(21)], "confidence_level": None}
        )


# --- Endpoint: suggested next action (Priority 5) -------------------------


def test_next_action_returns_actions_without_modifying_task(client):
    created = client.post(
        "/todos/", json={"title": "Write quarterly report", "context": "Q4", "subtasks": ["Gather data"]}
    ).json()
    todo_id = created["id"]
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        next_action_result=make_next_action(
            task_id=todo_id,
            actions=["Analyse the gathered data", "Draft the summary section"],
        )
    )
    response = client.post(f"/api/tasks/{todo_id}/next-action")
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == todo_id
    assert body["actions"] == ["Analyse the gathered data", "Draft the summary section"]
    assert 0 <= body["confidence_level"] <= 1

    # Rule 7: next-action must not modify the task
    stored = client.get(f"/todos/{todo_id}").json()
    assert stored["subtasks"] == ["Gather data"]

    # client picks an action and persists it via the existing update flow
    applied = client.put(f"/todos/{todo_id}", json={"subtasks": stored["subtasks"] + [body["actions"][0]]})
    assert applied.status_code == 200
    assert applied.json()["subtasks"] == ["Gather data", "Analyse the gathered data"]

    client.delete(f"/todos/{todo_id}")


def test_next_action_task_not_found_returns_404(client):
    assert client.post("/api/tasks/99999/next-action").status_code == 404


def test_next_action_not_configured_returns_503(client, monkeypatch):
    created = client.post("/todos/", json={"title": "task"}).json()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post(f"/api/tasks/{created['id']}/next-action")
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()
    client.delete(f"/todos/{created['id']}")


def test_next_action_provider_failure_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        next_action_error=services.AIProviderError("boom")
    )
    response = client.post(f"/api/tasks/{created['id']}/next-action")
    assert response.status_code == 502
    client.delete(f"/todos/{created['id']}")


def test_next_action_invalid_ai_response_returns_502(client):
    created = client.post("/todos/", json={"title": "task"}).json()
    app.dependency_overrides[services.get_task_parsing_service] = lambda: FakeParsingService(
        next_action_error=services.InvalidAIResponseError("bad json")
    )
    response = client.post(f"/api/tasks/{created['id']}/next-action")
    assert response.status_code == 502
    client.delete(f"/todos/{created['id']}")


# --- Service-level: next action parsing and validation --------------------


def test_service_next_action_happy_path_and_normalizes():
    raw = '{"actions": [" Analyse results ", "Draft summary"], "confidence_level": 0.9}'
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    result = service.next_action(5, "Write quarterly report", None, None, ["Gather data"])

    assert result.task_id == 5
    assert result.actions == ["Analyse results", "Draft summary"]
    assert result.confidence_level == 0.9


def test_service_next_action_rejects_empty_actions():
    raw = '{"actions": ["  ", ""], "confidence_level": 0.5}'
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    with pytest.raises(services.InvalidAIResponseError):
        service.next_action(1, "task", None, None, None)


def test_service_next_action_rejects_more_than_five():
    raw = '{"actions": ["a", "b", "c", "d", "e", "f"], "confidence_level": 0.5}'
    service = services.TaskParsingService(client=FakeGeminiClient(response=FakeGeminiResponse(raw)))

    with pytest.raises(services.InvalidAIResponseError):
        service.next_action(1, "task", None, None, None)


def test_next_action_result_rejects_out_of_range_confidence():
    with pytest.raises(ValidationError):
        schemas.TaskNextActionResult.model_validate(
            {"task_id": 1, "actions": ["do it"], "confidence_level": 1.5}
        )


def test_next_action_prompt_is_small_and_grounded():
    prompt = services.build_next_action_prompt("Write report", "Q4 numbers", "Finance", ["Gather data"])
    assert "1 to 3" in prompt
    assert "Do NOT invent" in prompt
    assert "Gather data" in prompt
    assert "Write report" in prompt


# --- GET /api/tasks/reminders (Priority 6: Smart Reminders) -----------------

_reminder_task_id = 0


def _reminder_task(**overrides):
    global _reminder_task_id
    _reminder_task_id += 1
    defaults = {"id": _reminder_task_id, "title": "Task",
                "due_date": datetime.now() + timedelta(days=1)}
    defaults.update(overrides)
    return models.Todo(**defaults)


def test_reminders_due_tomorrow_considers_status_and_context():
    # Due tomorrow + not started (no subtasks) -> escalated (Rules section 5.6)
    [escalated] = services.build_reminders([_reminder_task(subtasks=None, context="Q4 report")])
    assert escalated.level == "urgent"
    assert "escalated" in escalated.message
    assert "Context: Q4 report" in escalated.message
    # Due tomorrow + already started -> stays at the base level
    [started] = services.build_reminders([_reminder_task(subtasks=["step 1"])])
    assert started.level == "warning"
    assert "escalated" not in started.message


def test_reminders_overdue_and_due_today_are_urgent():
    overdue = _reminder_task(due_date=datetime.now() - timedelta(days=2), subtasks=["step 1"])
    today = _reminder_task(due_date=datetime.now(), subtasks=["step 1"])
    levels = {r.task_id: r.level for r in services.build_reminders([overdue, today])}
    assert set(levels.values()) == {"urgent"}
    messages = [r.message for r in services.build_reminders([overdue])]
    assert "Overdue by 2 day(s)." in messages[0]


def test_reminders_escalate_imminent_not_started_task():
    # Rules section 5.6 example: due tomorrow + not started -> increase urgency
    not_started = _reminder_task(subtasks=None)
    started = _reminder_task(subtasks=["step 1"])
    [escalated] = services.build_reminders([not_started])
    [normal] = services.build_reminders([started])
    assert escalated.level != normal.level
    assert "escalated" in escalated.message


def test_reminders_skip_completed_far_away_or_undated_tasks():
    tomorrow = datetime.now() + timedelta(days=1)
    tasks = [
        _reminder_task(due_date=tomorrow, completed=True),          # completed
        _reminder_task(due_date=datetime.now() + timedelta(days=30)),  # far away
        _reminder_task(due_date=None),                              # no deadline
        _reminder_task(due_date=tomorrow, subtasks=["step 1"]),     # the only hit
    ]
    reminders = services.build_reminders(tasks)
    assert len(reminders) == 1


def test_reminders_sorted_most_urgent_first():
    soon = _reminder_task(due_date=datetime.now(), subtasks=["step 1"])
    later = _reminder_task(due_date=datetime.now() + timedelta(days=3), subtasks=["step 1"])
    reminders = services.build_reminders([later, soon])
    assert [r.level for r in reminders] == ["urgent", "info"]


def test_reminders_endpoint_returns_list(client):
    created = client.post(
        "/todos/",
        json={"title": "Due tomorrow", "due_date": (datetime.now() + timedelta(days=1)).isoformat()},
    ).json()
    try:
        response = client.get("/api/tasks/reminders")
        assert response.status_code == 200
        body = response.json()
        assert any(reminder["task_id"] == created["id"] for reminder in body)
        assert all(reminder["level"] in {"info", "warning", "urgent"} for reminder in body)
    finally:
        client.delete(f"/todos/{created['id']}")


def test_reminders_endpoint_excludes_completed_tasks(client):
    created = client.post(
        "/todos/",
        json={
            "title": "Done task",
            "completed": True,
            "due_date": (datetime.now() - timedelta(days=1)).isoformat(),
        },
    ).json()
    try:
        body = client.get("/api/tasks/reminders").json()
        assert all(reminder["task_id"] != created["id"] for reminder in body)
    finally:
        client.delete(f"/todos/{created['id']}")