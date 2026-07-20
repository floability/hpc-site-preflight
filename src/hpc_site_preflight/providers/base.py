"""Provider-neutral contracts for structured model calls."""

from abc import ABC, abstractmethod
from typing import Any, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.reporting.tracker import RunTracker

ResultModel = TypeVar("ResultModel", bound=BaseModel)
ModelProviderName: TypeAlias = Literal["recorded", "openai", "anthropic", "gemini"]


class StructuredModelRequest(BaseModel):
    """Prompts and result-tool metadata shared by model providers."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    output_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    output_description: str = "Submit the structured result."


class StructuredModelResponse(BaseModel):
    """Locally validated structured output and provider-reported usage."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]
    response_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)

    def parse_as(self, result_type: type[ResultModel]) -> ResultModel:
        """Return the structured data as its caller-owned Pydantic type."""

        return result_type.model_validate(self.data)


class ModelProvider(ABC):
    """Small provider interface for one schema-constrained model call."""

    @abstractmethod
    def generate_structured(
        self,
        request: StructuredModelRequest,
        result_type: type[BaseModel],
        tracker: RunTracker,
    ) -> StructuredModelResponse:
        """Generate and locally validate one structured response."""
