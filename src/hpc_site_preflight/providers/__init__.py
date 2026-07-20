"""LLM-provider adapters used only by the documentation subsystem/controller."""

from hpc_site_preflight.providers.base import (
    ModelProvider,
    ModelProviderName,
    StructuredModelRequest,
    StructuredModelResponse,
)
from hpc_site_preflight.providers.openai import OpenAIProvider
from hpc_site_preflight.providers.recorded import RecordedModelProvider
from hpc_site_preflight.providers.registry import (
    create_live_model_provider,
    provider_for_model,
)

__all__ = [
    "ModelProvider",
    "ModelProviderName",
    "OpenAIProvider",
    "RecordedModelProvider",
    "StructuredModelRequest",
    "StructuredModelResponse",
    "create_live_model_provider",
    "provider_for_model",
]
