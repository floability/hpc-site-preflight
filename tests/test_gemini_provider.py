"""Offline tests for the minimal structured Gemini adapter."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, HttpUrl

from hpc_site_preflight.exceptions import ConfigurationError, ModelProviderError
from hpc_site_preflight.providers.base import StructuredModelRequest
from hpc_site_preflight.providers.gemini import GeminiProvider
from hpc_site_preflight.reporting.tracker import RunTracker


class ExampleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    evidence: str | None
    source_url: HttpUrl | None


def _tracker(tmp_path: Path, run_id: str = "gemini") -> RunTracker:
    return RunTracker(command="test", run_root=tmp_path, quiet=True, run_id=run_id)


def _request() -> StructuredModelRequest:
    return StructuredModelRequest(
        system_prompt="Return only grounded facts.",
        user_prompt="Extract the example value.",
        output_name="submit_result",
    )


def _response(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(result)}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 120,
            "candidatesTokenCount": 18,
            "thoughtsTokenCount": 7,
            "totalTokenCount": 145,
        },
    }


def _provider(
    payloads: list[tuple[int, dict[str, Any]]],
    captured: list[tuple[str, dict[str, Any]]],
    *,
    max_retries: int = 0,
) -> GeminiProvider:
    responses = iter(payloads)

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, json.loads(request.content)))
        status_code, payload = next(responses)
        return httpx.Response(status_code, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handle),
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    return GeminiProvider(
        model="models/gemini-test",
        client=client,
        max_retries=max_retries,
    )


def test_valid_response_is_locally_validated_and_tracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    captured: list[tuple[str, dict[str, Any]]] = []
    provider = _provider(
        [
            (
                200,
                _response(
                    {
                        "value": "documented",
                        "evidence": "guide",
                        "source_url": "https://example.edu/guide",
                    }
                ),
            )
        ],
        captured,
    )
    tracker = _tracker(tmp_path)

    result = provider.generate_structured(_request(), ExampleResult, tracker)

    assert result.value == "documented"
    assert str(result.source_url) == "https://example.edu/guide"
    path, payload = captured[0]
    assert path == "/v1beta/models/gemini-test:generateContent"
    assert payload["generationConfig"]["responseMimeType"] == "application/json"
    schema = payload["generationConfig"]["responseJsonSchema"]
    assert "format" not in schema["properties"]["source_url"]["anyOf"][0]

    stage = tracker.report.steps[0]
    assert stage.name == "structured_model_call"
    assert stage.status == "completed"
    assert stage.model_usage.input_tokens == 120
    assert stage.model_usage.output_tokens == 25
    assert stage.model_usage.usage_available is True


def test_nullable_partial_response_is_accepted(tmp_path: Path) -> None:
    provider = _provider(
        [
            (
                200,
                _response(
                    {
                        "value": "unknown",
                        "evidence": None,
                        "source_url": None,
                    }
                ),
            )
        ],
        [],
    )

    result = provider.generate_structured(_request(), ExampleResult, _tracker(tmp_path))

    assert result.value == "unknown"
    assert result.evidence is None
    assert result.source_url is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "invalid structured JSON"),
        (
            '{"value":"documented","evidence":null,"source_url":"invalid"}',
            "failed schema validation",
        ),
    ],
)
def test_invalid_structured_responses_fail_explicitly(
    text: str,
    message: str,
    tmp_path: Path,
) -> None:
    payload = _response({"value": "unused", "evidence": None, "source_url": None})
    payload["candidates"][0]["content"]["parts"][0]["text"] = text
    provider = _provider([(200, payload)], [])
    tracker = _tracker(tmp_path)

    with pytest.raises(ModelProviderError, match=message):
        provider.generate_structured(_request(), ExampleResult, tracker)

    assert tracker.report.steps[0].status == "failed"
    assert tracker.report.model_usage.requests == 1


def test_retry_is_bounded_and_recorded(tmp_path: Path) -> None:
    captured: list[tuple[str, dict[str, Any]]] = []
    provider = _provider(
        [
            (429, {"error": {"message": "retry"}}),
            (
                200,
                _response(
                    {
                        "value": "documented",
                        "evidence": None,
                        "source_url": None,
                    }
                ),
            ),
        ],
        captured,
        max_retries=1,
    )
    tracker = _tracker(tmp_path)

    provider.generate_structured(_request(), ExampleResult, tracker)

    stage = tracker.report.steps[0]
    assert len(captured) == 2
    assert stage.retries == 1
    assert stage.model_usage.requests == 2
    assert stage.model_usage.usage_available is False


def test_live_client_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
        GeminiProvider(model="gemini-test")
