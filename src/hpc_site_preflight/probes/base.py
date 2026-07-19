"""Provider-neutral approved-pilot interfaces and result contracts."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class PilotResultBundle(BaseModel):
    """Normalized results from approved operational capability tests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    site_id: str
    mode: str
    results: dict[str, Any] = Field(default_factory=dict)


class PilotProvider(ABC):
    """Load or run only predefined bounded pilots."""

    @abstractmethod
    def collect(self, site: SiteInfo, tracker: RunTracker) -> PilotResultBundle:
        """Return normalized pilot results."""
