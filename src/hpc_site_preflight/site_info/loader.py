"""Load and validate site information from JSON."""

import json
from pathlib import Path

from pydantic import ValidationError

from hpc_site_preflight.exceptions import ConfigurationError
from hpc_site_preflight.site_info.models import SiteInfo


def load_site_info(path: Path) -> SiteInfo:
    """Load one strict site-information file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Could not read site information: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Site information is invalid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc
    try:
        return SiteInfo.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationError(
            f"Site information failed {exc.error_count()} contract validation(s)."
        ) from exc
