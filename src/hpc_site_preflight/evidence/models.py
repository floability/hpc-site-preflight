"""Detailed evidence-report contracts."""

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

EvidenceSource = Literal["simulated", "measured"]
EvidenceScalar: TypeAlias = str | int | float | bool
EvidenceValue: TypeAlias = EvidenceScalar | list[EvidenceScalar]


class EvidenceRecord(BaseModel):
    """One accepted, rejected, or invalid evidence record."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    field_path: str
    source_type: Literal["profile", "measurement", "documentation", "pilot", "user"]
    scope: Literal["target_site", "sibling_site", "out_of_scope", "unknown"]
    trust: Literal["official", "captured", "curated", "illustrative", "user", "unknown"]
    disposition: Literal["accepted", "rejected", "invalid"]
    value: EvidenceValue | None = None
    observed_at: datetime | None = None
    freshness: Literal["per_run", "session", "daily", "site_change", "unknown"]
    source_reference: str | None = None
    documentation_url: str | None = None
    documentation_heading: str | None = None
    exact_quote: str | None = Field(default=None, max_length=2000)
    chunk_id: str | None = None
    command_id: str | None = None
    pilot_id: str | None = None
    result: EvidenceValue | None = None
    reason: str | None = None


class ConflictRecord(BaseModel):
    """Detailed evidence conflict and deterministic selection."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    selected_evidence_id: str
    other_evidence_ids: list[str]
    selection_rule: str
    note: str


class EvidenceLink(BaseModel):
    """Links from one compact profile field to evidence records."""

    model_config = ConfigDict(extra="forbid")

    profile_field: str
    evidence_ids: list[str] = Field(min_length=1)


class UnresolvedAction(BaseModel):
    """The deterministic next action for an unresolved field."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    action: Literal[
        "login_measurement",
        "run_pilot",
        "additional_documentation",
        "user_input",
        "admin_confirmation",
    ]
    action_id: str
    reason: str


# Backward-compatible name used by the initial evidence bundle placeholder.
EvidenceItem = EvidenceRecord
