"""Provider-neutral interface for the documentation policy subsystem."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class DocumentationEvidence(BaseModel):
    """Documentation-derived partial policy plus detailed provenance references."""

    model_config = ConfigDict(extra="forbid")

    site_id: str
    context_mode: str
    partial_policy: dict[str, Any] = Field(default_factory=dict)
    evidence_report: dict[str, Any] = Field(default_factory=dict)


class DocumentationPolicyProvider(ABC):
    """Build documentation evidence without exposing provider-specific SDK types."""

    @abstractmethod
    def build(
        self,
        site: SiteInfo,
        tracker: RunTracker,
        *,
        context_mode: str,
    ) -> DocumentationEvidence:
        """Return validated documentation evidence."""
