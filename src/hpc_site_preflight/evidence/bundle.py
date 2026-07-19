"""Detailed evidence-report envelope."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.evidence.models import (
    ConflictRecord,
    EvidenceLink,
    EvidenceRecord,
    UnresolvedAction,
)


class EvidenceReport(BaseModel):
    """Auditable evidence emitted beside a compact site profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"]
    report_id: str
    site_id: str
    generated_at: datetime
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    links: list[EvidenceLink] = Field(default_factory=list)
    unresolved: list[UnresolvedAction] = Field(default_factory=list)


class SiteEvidenceBundle(EvidenceReport):
    """Compatibility name for the complete detailed evidence artifact."""
