"""Exercises the real SDK request path against a fake transport. No key or network needed."""

import json

import httpx2 as httpx
import openai
import pytest

from eschool_assistant.interpreter.base import InterpretationError
from eschool_assistant.interpreter.gemini import GeminiInterpreter, output_schema
from eschool_assistant.models import Gender, ParentRequest


def api_message(text: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gemini-3.5-flash-lite",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": text or None},
            }
        ],
    }


def interpreter(handler) -> GeminiInterpreter:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return GeminiInterpreter(
        client=openai.OpenAI(
            api_key="test", base_url="https://test.local/", http_client=http, max_retries=0
        )
    )


def complete_output(**overrides) -> str:
    empty = {name: None for name in ParentRequest.model_fields}
    lists = (
        "area_counties",
        "levels",
        "curriculums",
        "facilities",
        "religions",
        "unsupported_preferences",
        "assumptions",
        "clarifying_questions",
    )
    empty |= {name: [] for name in lists}
    for name in ParentRequest.model_fields:
        if name.endswith("_importance"):
            empty[name] = "required"
    empty |= {"is_school_search": True, "fee_period_stated": "not_stated"}
    return json.dumps(empty | overrides)


def test_every_field_is_required_in_the_schema():
    schema = output_schema()
    assert set(schema["required"]) == set(ParentRequest.model_fields)
    assert schema["additionalProperties"] is False


def test_parses_valid_output_and_sends_the_expected_request():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=api_message(complete_output(gender="girls")))

    result = interpreter(handler).interpret("girls school")
    assert result.gender == Gender.GIRLS
    assert seen["body"]["model"] == "gemini-3.5-flash-lite"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert "girls school" in seen["body"]["messages"][1]["content"]


def test_output_outside_the_contract_is_rejected():
    def handler(request):
        return httpx.Response(200, json=api_message(complete_output(gender="unicorn")))

    with pytest.raises(InterpretationError):
        interpreter(handler).interpret("x")


def test_invalid_json_is_rejected():
    def handler(request):
        return httpx.Response(200, json=api_message("not json"))

    with pytest.raises(InterpretationError):
        interpreter(handler).interpret("x")


def test_refusal_is_handled():
    def handler(request):
        return httpx.Response(200, json=api_message("", "content_filter"))

    with pytest.raises(InterpretationError):
        interpreter(handler).interpret("x")


def test_truncated_output_is_handled():
    def handler(request):
        return httpx.Response(200, json=api_message('{"is_sch', "length"))

    with pytest.raises(InterpretationError):
        interpreter(handler).interpret("x")


@pytest.mark.parametrize("status", [401, 429, 500])
def test_api_errors_become_interpretation_errors(status):
    def handler(request):
        body = {"error": {"message": "x"}}
        return httpx.Response(status, json=body)

    with pytest.raises(InterpretationError):
        interpreter(handler).interpret("x")
