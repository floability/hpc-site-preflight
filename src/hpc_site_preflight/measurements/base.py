"""Provider-neutral measurement interfaces and result contracts."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class MeasurementBundle(BaseModel):
    """Normalized login-node and scheduler measurements."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    site_id: str
    mode: str
    common: dict[str, Any] = Field(default_factory=dict)
    scheduler: dict[str, Any] = Field(default_factory=dict)
    storage: dict[str, Any] = Field(default_factory=dict)


class MeasurementProvider(ABC):
    """Load or capture measurement evidence through one stable interface."""

    @abstractmethod
    def collect(self, site: SiteInfo, tracker: RunTracker) -> MeasurementBundle:
        """Return normalized measurements."""
