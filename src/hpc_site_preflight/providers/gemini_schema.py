"""Small JSON Schema compatibility helper for Gemini structured output."""

from copy import deepcopy
from typing import Any


def gemini_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove Pydantic annotations unsupported by Gemini response schemas."""

    compatible = deepcopy(schema)
    _remove_unsupported_annotations(compatible)
    return compatible


def _remove_unsupported_annotations(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("default", None)
        value.pop("format", None)
        for child in value.values():
            _remove_unsupported_annotations(child)
    elif isinstance(value, list):
        for child in value:
            _remove_unsupported_annotations(child)
