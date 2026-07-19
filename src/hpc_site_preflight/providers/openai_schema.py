"""Small JSON Schema compatibility helper for OpenAI function tools."""

from copy import deepcopy
from typing import Any

_SUPPORTED_STRING_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "ipv4", "ipv6", "uuid"}
)


def openai_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Remove string formats unsupported by OpenAI structured outputs."""

    compatible = deepcopy(schema)
    _remove_unsupported_formats(compatible)
    return compatible


def _remove_unsupported_formats(value: Any) -> None:
    if isinstance(value, dict):
        schema_format = value.get("format")
        if isinstance(schema_format, str) and schema_format not in _SUPPORTED_STRING_FORMATS:
            value.pop("format")
        for child in value.values():
            _remove_unsupported_formats(child)
    elif isinstance(value, list):
        for child in value:
            _remove_unsupported_formats(child)
