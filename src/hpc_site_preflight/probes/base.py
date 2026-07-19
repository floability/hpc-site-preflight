"""Provider-neutral approved-pilot interfaces and result contracts."""

from abc import ABC, abstractmethod
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hpc_site_preflight.evidence.models import EvidenceMode, FixtureOrigin
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class PilotResultBundle(BaseModel):
    """Normalized results from approved operational capability tests."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    site_id: str
    mode: EvidenceMode
    fixture_origin: FixtureOrigin | None = None
    results: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_fixture_origin(self) -> Self:
        """Require origin metadata only for fixture evidence."""

        if self.mode == "fixture" and self.fixture_origin is None:
            raise ValueError("fixture_origin is required when mode is 'fixture'.")
        if self.mode == "live" and self.fixture_origin is not None:
            raise ValueError("fixture_origin must be omitted when mode is 'live'.")
        return self


class PilotProvider(ABC):
    """Load or run only predefined bounded pilots."""

    @abstractmethod
    def collect(self, site: SiteInfo, tracker: RunTracker) -> PilotResultBundle:
        """Return normalized pilot results."""
