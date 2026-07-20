"""Provider inference stays separate from CLI and provider adapters."""

import pytest

from hpc_site_preflight.exceptions import ConfigurationError, FeatureNotImplementedError
from hpc_site_preflight.providers.registry import (
    create_live_model_provider,
    provider_for_model,
)


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gpt-5-mini", "openai"),
        ("o3-mini", "openai"),
        ("claude-sonnet-4", "anthropic"),
        ("gemini-2.5-pro", "gemini"),
    ],
)
def test_provider_is_inferred_from_model(model: str, provider: str) -> None:
    assert provider_for_model(model) == provider


def test_unknown_model_fails_explicitly() -> None:
    with pytest.raises(ConfigurationError, match="Cannot infer"):
        provider_for_model("mystery-model")


@pytest.mark.parametrize("model", ["claude-sonnet-4", "gemini-2.5-pro"])
def test_future_provider_adapter_fails_explicitly(model: str) -> None:
    with pytest.raises(FeatureNotImplementedError, match="not implemented"):
        create_live_model_provider(model)
