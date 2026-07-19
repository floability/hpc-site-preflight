"""LLM-provider adapters used only by the documentation subsystem/controller."""

from hpc_site_preflight.providers.base import (
    ModelProvider,
    StructuredModelRequest,
    StructuredModelResponse,
)
from hpc_site_preflight.providers.openai import OpenAIProvider
from hpc_site_preflight.providers.recorded import RecordedModelProvider

__all__ = [
    "ModelProvider",
    "OpenAIProvider",
    "RecordedModelProvider",
    "StructuredModelRequest",
    "StructuredModelResponse",
]
