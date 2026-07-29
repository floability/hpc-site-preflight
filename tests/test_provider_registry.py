"""Provider inference stays separate from CLI and provider adapters."""

import pytest

from hpc_site_preflight.exceptions import ConfigurationError, FeatureNotImplementedError
from hpc_site_preflight.providers.gemini import GeminiProvider
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


def test_gemini_provider_is_constructed_from_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    provider = create_live_model_provider("gemini-3.5-flash")

    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-3.5-flash"


def test_future_anthropic_adapter_fails_explicitly() -> None:
    with pytest.raises(FeatureNotImplementedError, match="not implemented"):
        create_live_model_provider("claude-sonnet-4")
