"""OpenAI adapter placeholder; migrate tested retry logic in Milestone 4."""

from hpc_site_preflight.exceptions import FeatureNotImplementedError
from hpc_site_preflight.providers.base import ModelProvider, ModelTextResponse
from hpc_site_preflight.reporting.tracker import RunTracker


class OpenAIProvider(ModelProvider):
    """Use the OpenAI Responses API after Milestone 4 migration."""

    def generate(self, prompt: str, tracker: RunTracker) -> ModelTextResponse:
        raise FeatureNotImplementedError("The OpenAI adapter is planned for Milestone 4.")
