"""Minimal OpenAI Responses API adapter for structured calls."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from hpc_site_preflight.exceptions import ConfigurationError, ModelProviderError
from hpc_site_preflight.providers.base import (
    ModelProvider,
    ResultModel,
    StructuredModelRequest,
)
from hpc_site_preflight.providers.openai_schema import openai_compatible_schema
from hpc_site_preflight.reporting.tracker import RunTracker

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_RETRYABLE_STATUS_CODES = {408, 409, 429}


class _OpenAIUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int
    output_tokens: int


class _OpenAIOutputItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    name: str | None = None
    arguments: str | None = None


class _OpenAIResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    output: list[_OpenAIOutputItem]
    usage: _OpenAIUsage | None = None


class OpenAIProvider(ModelProvider):
    """Request one forced function result and validate it locally."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 600.0,
        max_retries: int = 0,
        client: httpx.Client | None = None,
    ) -> None:
        if max_retries < 0:
            raise ConfigurationError("max_retries must be non-negative.")

        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if client is None and not resolved_key:
            raise ConfigurationError("OPENAI_API_KEY is required for live OpenAI requests.")

        self.model = model
        self.max_retries = max_retries
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {resolved_key}"},
        )

    def generate_structured(
        self,
        request: StructuredModelRequest,
        result_type: type[ResultModel],
        tracker: RunTracker,
    ) -> ResultModel:
        """Make one tracked request and return schema-validated data."""

        with tracker.stage("structured_model_call"):
            response, attempts = self._send(request, result_type, tracker)
            envelope = self._decode_response(response, attempts, tracker)
            self._record_usage(envelope, attempts, tracker)
            return self._extract_data(envelope, request.output_name, result_type)

    def _send(
        self,
        request: StructuredModelRequest,
        result_type: type[BaseModel],
        tracker: RunTracker,
    ) -> tuple[httpx.Response, int]:
        payload = self._payload(request, result_type)
        attempts = 0

        while attempts <= self.max_retries:
            attempts += 1
            try:
                response = self._client.post("/responses", json=payload)
            except httpx.RequestError as exc:
                if attempts <= self.max_retries:
                    tracker.record_retry()
                    continue
                tracker.record_model_usage(requests=attempts, usage_available=False)
                raise ModelProviderError(
                    "OpenAI request failed before a response was received."
                ) from exc

            if self._is_retryable(response.status_code) and attempts <= self.max_retries:
                tracker.record_retry()
                continue
            if response.is_error:
                tracker.record_model_usage(requests=attempts, usage_available=False)
                raise ModelProviderError(
                    f"OpenAI request failed with HTTP status {response.status_code}."
                )
            return response, attempts

        raise RuntimeError("OpenAI retry loop ended unexpectedly.")

    def _decode_response(
        self,
        response: httpx.Response,
        attempts: int,
        tracker: RunTracker,
    ) -> _OpenAIResponse:
        try:
            payload = response.json()
            return _OpenAIResponse.model_validate(payload)
        except (ValueError, ValidationError) as exc:
            tracker.record_model_usage(requests=attempts, usage_available=False)
            raise ModelProviderError("OpenAI returned an invalid response envelope.") from exc

    @staticmethod
    def _record_usage(
        response: _OpenAIResponse,
        attempts: int,
        tracker: RunTracker,
    ) -> None:
        usage = response.usage
        tracker.record_model_usage(
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            requests=attempts,
            usage_available=usage is not None and attempts == 1,
        )

    @staticmethod
    def _extract_data(
        response: _OpenAIResponse,
        output_name: str,
        result_type: type[ResultModel],
    ) -> ResultModel:
        calls = [
            item
            for item in response.output
            if item.type == "function_call" and item.name == output_name
        ]
        if len(calls) != 1 or calls[0].arguments is None:
            raise ModelProviderError(f"Expected exactly one {output_name} function call.")

        try:
            raw_data = json.loads(calls[0].arguments)
        except json.JSONDecodeError as exc:
            raise ModelProviderError("OpenAI returned invalid structured JSON.") from exc

        try:
            return result_type.model_validate(raw_data)
        except ValidationError as exc:
            raise ModelProviderError("OpenAI structured output failed schema validation.") from exc

    def _payload(
        self,
        request: StructuredModelRequest,
        result_type: type[BaseModel],
    ) -> dict[str, Any]:
        result_tool = {
            "type": "function",
            "name": request.output_name,
            "description": request.output_description,
            "parameters": openai_compatible_schema(result_type.model_json_schema()),
            "strict": True,
        }
        return {
            "model": self.model,
            "instructions": request.system_prompt,
            "input": [{"role": "user", "content": request.user_prompt}],
            "tools": [result_tool],
            "tool_choice": {"type": "function", "name": request.output_name},
            "parallel_tool_calls": False,
            "store": False,
        }

    @staticmethod
    def _is_retryable(status_code: int) -> bool:
        return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500
