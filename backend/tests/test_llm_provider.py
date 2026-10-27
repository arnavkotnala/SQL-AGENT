"""Gemini provider error handling (API errors, rate limits, bad JSON) with a fake SDK client."""
import pytest
from google.genai import errors

from app.config import Settings
from app.llm.provider import (GeminiProvider, LLMNotConfiguredError, LLMRateLimitError, LLMResponseError,
                              LLMServiceError)
from app.llm.schemas import GoalInterpretation, SqlProposal, to_gemini_schema

VALID = ('{"interpreted_goal": "g", "operation": "read", "feasible": true, "infeasible_reason": null, '
         '"needs_clarification": false, "clarification_question": null, "success_criteria": ["a"], '
         '"requested_columns": [], "relevant_tables": ["students"]}')


class Resp:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        o = self.outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return Resp(o)


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def _provider(outcomes, retries=3):
    s = Settings(_env_file=None, gemini_api_key="test-key", gemini_max_retries=retries, gemini_model="test-model")
    sleeps = []
    p = GeminiProvider(s, client=FakeClient(outcomes), sleep=sleeps.append)
    return p, sleeps


def _api_error(code, status="ERR"):
    cls = errors.ClientError if code < 500 else errors.ServerError
    return cls(code, {"error": {"code": code, "message": f"boom {code}", "status": status}})


def call(p):
    return p.generate_structured(task="interpret", system="s", prompt="p", schema=GoalInterpretation)


def test_success_uses_configured_model_and_json_schema():
    p, _ = _provider([VALID])
    out = call(p)
    assert out.interpreted_goal == "g"
    c = p._client.models.calls[0]
    assert c["model"] == "test-model"
    assert c["config"].response_mime_type == "application/json"
    assert c["config"].response_json_schema["properties"]["operation"]["enum"] == ["read", "modify"]


def test_rate_limit_is_retried_with_backoff_then_succeeds():
    p, sleeps = _provider([_api_error(429, "RESOURCE_EXHAUSTED"), _api_error(429), VALID])
    assert call(p).operation == "read"
    assert sleeps == [1.5, 3.0]


def test_persistent_rate_limit_raises_rate_limit_error():
    p, sleeps = _provider([_api_error(429)] * 4)
    with pytest.raises(LLMRateLimitError, match="429"):
        call(p)
    assert len(sleeps) == 3


def test_server_error_retried():
    p, _ = _provider([_api_error(503, "UNAVAILABLE"), VALID])
    assert call(p).interpreted_goal == "g"


def test_auth_error_is_not_retried():
    p, sleeps = _provider([_api_error(403, "PERMISSION_DENIED"), VALID])
    with pytest.raises(LLMServiceError, match="API key"):
        call(p)
    assert sleeps == []


def test_unknown_model_gives_actionable_message():
    p, _ = _provider([_api_error(404, "NOT_FOUND")])
    with pytest.raises(LLMServiceError, match="GEMINI_MODEL"):
        call(p)


def test_invalid_json_is_retried_then_reported():
    p, _ = _provider(["not json", '{"interpreted_goal": 1}', "```json\n" + VALID + "\n```"], retries=2)
    assert call(p).interpreted_goal == "g"  # code fences tolerated
    p2, _ = _provider(["nope", "nope", "nope"], retries=2)
    with pytest.raises(LLMResponseError):
        call(p2)


def test_missing_api_key_raises_not_configured():
    with pytest.raises(LLMNotConfiguredError):
        GeminiProvider(Settings(_env_file=None, gemini_api_key=None))


def test_schemas_are_gemini_compatible():
    for model in (GoalInterpretation, SqlProposal):
        s = str(to_gemini_schema(model))
        assert "$ref" not in s and "anyOf" not in s and "'title'" not in s


def test_rate_limit_honours_server_retry_delay():
    err = errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
                                             "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                          "retryDelay": "37s"}]}})
    p, sleeps = _provider([err, VALID])
    assert call(p).interpreted_goal == "g"
    assert sleeps == [37.0]
    huge = errors.ClientError(429, {"error": {"code": 429, "details": [{"retryDelay": "900s"}]}})
    p2, sleeps2 = _provider([huge, VALID])
    call(p2)
    assert sleeps2 == [60.0]  # capped
