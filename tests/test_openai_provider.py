"""Offline tests for the minimal structured OpenAI adapter."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, HttpUrl

from hpc_site_preflight.exceptions import ConfigurationError, ModelProviderError
from hpc_site_preflight.providers.base import StructuredModelRequest
from hpc_site_preflight.providers.openai import OpenAIProvider
from hpc_site_preflight.reporting.tracker import RunTracker

ROOT = Path(__file__).resolve().parents[1]
RECORDED = ROOT / "tests" / "recorded" / "openai"


class ExampleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    evidence: str | None
    source_url: HttpUrl | None


def _load(name: str) -> dict[str, Any]:
    return json.loads((RECORDED / name).read_text(encoding="utf-8"))


def _tracker(tmp_path: Path, run_id: str = "model") -> RunTracker:
    return RunTracker(command="test", run_root=tmp_path, quiet=True, run_id=run_id)


def _request() -> StructuredModelRequest:
    return StructuredModelRequest(
        system_prompt="Return only grounded facts.",
        user_prompt="Extract the example value.",
        output_name="submit_result",
    )


def _provider(
    payloads: list[tuple[int, dict[str, Any]]],
    captured: list[dict[str, Any]],
    *,
    max_retries: int = 0,
) -> OpenAIProvider:
    responses = iter(payloads)

    def handle(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        status_code, payload = next(responses)
        return httpx.Response(status_code, json=payload)

    client = httpx.Client(
        transport=httpx.MockTransport(handle),
        base_url="https://api.openai.com/v1",
    )
    return OpenAIProvider(model="test-model", client=client, max_retries=max_retries)


def test_valid_recorded_response_is_locally_validated_and_tracked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    captured: list[dict[str, Any]] = []
    provider = _provider([(200, _load("valid.json"))], captured)
    tracker = _tracker(tmp_path)

    result = provider.generate_structured(_request(), ExampleResult, tracker)

    assert result.value == "documented"
    assert str(result.source_url) == "https://example.edu/guide"
    assert captured[0]["tool_choice"] == {"type": "function", "name": "submit_result"}
    tool = captured[0]["tools"][0]
    assert tool["strict"] is True
    assert "format" not in tool["parameters"]["properties"]["source_url"]["anyOf"][0]

    stage = tracker.report.steps[0]
    assert stage.name == "structured_model_call"
    assert stage.status == "completed"
    assert stage.duration_seconds is not None
    assert stage.model_usage.input_tokens == 120
    assert stage.model_usage.output_tokens == 18
    assert stage.model_usage.usage_available is True


def test_nullable_partial_recorded_response_is_accepted(tmp_path: Path) -> None:
    provider = _provider([(200, _load("partial.json"))], [])

    result = provider.generate_structured(_request(), ExampleResult, _tracker(tmp_path))

    assert result.value == "unknown"
    assert result.evidence is None
    assert result.source_url is None


@pytest.mark.parametrize(
    ("recording", "message"),
    [
        ("invalid_schema.json", "failed schema validation"),
        ("invalid_json.json", "invalid structured JSON"),
    ],
)
def test_invalid_recorded_responses_fail_explicitly(
    recording: str,
    message: str,
    tmp_path: Path,
) -> None:
    provider = _provider([(200, _load(recording))], [])
    tracker = _tracker(tmp_path)

    with pytest.raises(ModelProviderError, match=message):
        provider.generate_structured(_request(), ExampleResult, tracker)

    assert tracker.report.steps[0].status == "failed"
    assert tracker.report.model_usage.requests == 1


def test_retry_is_bounded_and_recorded(tmp_path: Path) -> None:
    captured: list[dict[str, Any]] = []
    provider = _provider(
        [(429, {"error": {"message": "retry"}}), (200, _load("valid.json"))],
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
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
        OpenAIProvider(model="test-model")
