"""Aggregate all evidence sources behind one fixture/live-neutral contract."""

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.evidence.models import EvidenceItem, UnresolvedAction


class SiteEvidenceBundle(BaseModel):
    """Complete evidence state used by reconciliation and evaluation."""

    model_config = ConfigDict(extra="forbid")

    site_id: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    unresolved: list[UnresolvedAction] = Field(default_factory=list)
