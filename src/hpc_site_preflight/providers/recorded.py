"""Recorded structured model responses for offline simulation."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hpc_site_preflight.exceptions import ModelProviderError
from hpc_site_preflight.providers.base import (
    ModelProvider,
    StructuredModelRequest,
    StructuredModelResponse,
)
from hpc_site_preflight.reporting.tracker import RunTracker


class RecordedModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_name: str
    data: dict[str, Any]
    response_id: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ModelRecording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"]
    note: str
    responses: list[RecordedModelResponse]


class RecordedModelProvider(ModelProvider):
    """Return reviewed model responses in their recorded order."""

    def __init__(self, recording: ModelRecording) -> None:
        self.recording = recording
        self._index = 0

    @classmethod
    def from_path(cls, path: Path) -> "RecordedModelProvider":
        try:
            recording = ModelRecording.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ModelProviderError(f"Could not read model recording: {path}") from exc
        except ValidationError as exc:
            raise ModelProviderError(
                f"Model recording failed {exc.error_count()} contract validation(s)."
            ) from exc
        return cls(recording)

    def generate_structured(
        self,
        request: StructuredModelRequest,
        result_type: type[BaseModel],
        tracker: RunTracker,
    ) -> StructuredModelResponse:
        with tracker.stage("structured_model_call"):
            if self._index >= len(self.recording.responses):
                raise ModelProviderError("Model recording has no response left for this call.")
            recorded = self.recording.responses[self._index]
            self._index += 1
            if recorded.output_name != request.output_name:
                raise ModelProviderError(
                    f"Expected recorded output '{request.output_name}', "
                    f"found '{recorded.output_name}'."
                )
            try:
                result = result_type.model_validate(recorded.data)
            except ValidationError as exc:
                raise ModelProviderError("Recorded model output failed schema validation.") from exc

            usage_available = (
                recorded.input_tokens is not None and recorded.output_tokens is not None
            )
            tracker.record_model_usage(
                input_tokens=recorded.input_tokens or 0,
                output_tokens=recorded.output_tokens or 0,
                usage_available=usage_available,
            )
            return StructuredModelResponse(
                data=result.model_dump(mode="json"),
                response_id=recorded.response_id,
                input_tokens=recorded.input_tokens,
                output_tokens=recorded.output_tokens,
            )
