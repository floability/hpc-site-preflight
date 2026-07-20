"""Map provider-neutral model identifiers to provider adapters."""

from hpc_site_preflight.exceptions import ConfigurationError, FeatureNotImplementedError
from hpc_site_preflight.providers.base import ModelProvider, ModelProviderName
from hpc_site_preflight.providers.openai import OpenAIProvider

MODEL_PROVIDER_PREFIXES: tuple[tuple[str, ModelProviderName], ...] = (
    ("gpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("claude-", "anthropic"),
    ("gemini-", "gemini"),
    ("models/gemini-", "gemini"),
)


def provider_for_model(model: str) -> ModelProviderName:
    """Return the provider implied by a reviewed model-name prefix."""

    normalized = model.strip().lower()
    for prefix, provider in MODEL_PROVIDER_PREFIXES:
        if normalized.startswith(prefix):
            return provider
    raise ConfigurationError(
        f"Cannot infer a provider from model '{model}'. "
        "Expected an OpenAI gpt-/o-series, Anthropic claude-, or Gemini gemini- model."
    )


def create_live_model_provider(model: str) -> ModelProvider:
    """Construct the implemented adapter for a provider-neutral model name."""

    provider = provider_for_model(model)
    if provider == "openai":
        return OpenAIProvider(model=model)
    raise FeatureNotImplementedError(
        f"The {provider} adapter for model '{model}' is not implemented yet."
    )
