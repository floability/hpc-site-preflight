"""Provider-neutral model response and token-usage interfaces."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict

from hpc_site_preflight.reporting.tracker import RunTracker


class ModelTextResponse(BaseModel):
    """Provider-neutral text response with optional reported usage."""

    model_config = ConfigDict(extra="forbid")

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ModelProvider(ABC):
    """Small provider interface that reports usage to the shared tracker."""

    @abstractmethod
    def generate(self, prompt: str, tracker: RunTracker) -> ModelTextResponse:
        """Generate one response and report provider usage."""
