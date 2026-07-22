"""Load and validate site descriptor from JSON."""

import json
from pathlib import Path

from pydantic import ValidationError

from hpc_site_preflight.exceptions import ConfigurationError
from hpc_site_preflight.site_descriptor.models import SiteDescriptor


def load_site_descriptor(path: Path) -> SiteDescriptor:
    """Load one strict site descriptor file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Could not read site descriptor: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Site descriptor is invalid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc
    try:
        return SiteDescriptor.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationError(
            f"Site descriptor failed {exc.error_count()} contract validation(s)."
        ) from exc
