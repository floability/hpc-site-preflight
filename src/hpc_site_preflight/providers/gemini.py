"""Minimal Gemini GenerateContent adapter for structured calls."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hpc_site_preflight.exceptions import ConfigurationError, ModelProviderError
from hpc_site_preflight.providers.base import (
    ModelProvider,
    ResultModel,
    StructuredModelRequest,
)
from hpc_site_preflight.providers.gemini_schema import gemini_compatible_schema
from hpc_site_preflight.reporting.tracker import RunTracker

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_RETRYABLE_STATUS_CODES = {408, 429}


class _GeminiPart(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    thought: bool = False


class _GeminiContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    parts: list[_GeminiPart] = Field(default_factory=list)


class _GeminiCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    content: _GeminiContent | None = None
    finish_reason: str | None = Field(default=None, alias="finishReason")


class _GeminiUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    prompt_token_count: int | None = Field(default=None, alias="promptTokenCount")
    candidates_token_count: int | None = Field(default=None, alias="candidatesTokenCount")
    thoughts_token_count: int = Field(default=0, alias="thoughtsTokenCount")


class _GeminiResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    candidates: list[_GeminiCandidate] = Field(default_factory=list)
    usage_metadata: _GeminiUsage | None = Field(default=None, alias="usageMetadata")


class GeminiProvider(ModelProvider):
    """Request one JSON-schema response and validate it locally."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 300.0,
        max_retries: int = 0,
        client: httpx.Client | None = None,
    ) -> None:
        if max_retries < 0:
            raise ConfigurationError("max_retries must be non-negative.")

        self.model = self._normalize_model(model)
        resolved_key = api_key or os.getenv("GEMINI_API_KEY")
        self.max_retries = max_retries
        if client is not None:
            self._client = client
        else:
            if not resolved_key:
                raise ConfigurationError(
                    "GEMINI_API_KEY is required for live Gemini requests."
                )
            self._client = httpx.Client(
                base_url=base_url,
                timeout=timeout,
                headers={"x-goog-api-key": resolved_key},
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
            return self._extract_data(envelope, result_type)

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
                response = self._client.post(
                    f"/models/{self.model}:generateContent",
                    json=payload,
                )
            except httpx.RequestError as exc:
                if attempts <= self.max_retries:
                    tracker.record_retry()
                    continue
                tracker.record_model_usage(requests=attempts, usage_available=False)
                raise ModelProviderError(
                    "Gemini request failed before a response was received."
                ) from exc

            if self._is_retryable(response.status_code) and attempts <= self.max_retries:
                tracker.record_retry()
                continue
            if response.is_error:
                tracker.record_model_usage(requests=attempts, usage_available=False)
                raise ModelProviderError(
                    f"Gemini request failed with HTTP status {response.status_code}."
                )
            return response, attempts

        raise RuntimeError("Gemini retry loop ended unexpectedly.")

    @staticmethod
    def _decode_response(
        response: httpx.Response,
        attempts: int,
        tracker: RunTracker,
    ) -> _GeminiResponse:
        try:
            return _GeminiResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            tracker.record_model_usage(requests=attempts, usage_available=False)
            raise ModelProviderError("Gemini returned an invalid response envelope.") from exc

    @staticmethod
    def _record_usage(
        response: _GeminiResponse,
        attempts: int,
        tracker: RunTracker,
    ) -> None:
        usage = response.usage_metadata
        usage_available = (
            usage is not None
            and usage.prompt_token_count is not None
            and usage.candidates_token_count is not None
            and attempts == 1
        )
        tracker.record_model_usage(
            input_tokens=usage.prompt_token_count if usage and usage.prompt_token_count else 0,
            output_tokens=(
                usage.candidates_token_count + usage.thoughts_token_count
                if usage and usage.candidates_token_count is not None
                else 0
            ),
            requests=attempts,
            usage_available=usage_available,
        )

    @staticmethod
    def _extract_data(
        response: _GeminiResponse,
        result_type: type[ResultModel],
    ) -> ResultModel:
        if len(response.candidates) != 1 or response.candidates[0].content is None:
            raise ModelProviderError("Expected exactly one Gemini response candidate.")

        parts = [
            part.text
            for part in response.candidates[0].content.parts
            if part.text is not None and not part.thought
        ]
        if not parts:
            raise ModelProviderError("Gemini returned no structured response text.")

        try:
            raw_data = json.loads("".join(parts))
        except json.JSONDecodeError as exc:
            raise ModelProviderError("Gemini returned invalid structured JSON.") from exc

        try:
            return result_type.model_validate(raw_data)
        except ValidationError as exc:
            raise ModelProviderError("Gemini structured output failed schema validation.") from exc

    @staticmethod
    def _payload(
        request: StructuredModelRequest,
        result_type: type[BaseModel],
    ) -> dict[str, Any]:
        return {
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": request.user_prompt}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": gemini_compatible_schema(
                    result_type.model_json_schema()
                ),
            },
        }

    @staticmethod
    def _normalize_model(model: str) -> str:
        normalized = model.removeprefix("models/").strip()
        if not normalized or "/" in normalized:
            raise ConfigurationError(f"Invalid Gemini model identifier: '{model}'.")
        return normalized

    @staticmethod
    def _is_retryable(status_code: int) -> bool:
        return status_code in _RETRYABLE_STATUS_CODES or status_code >= 500
