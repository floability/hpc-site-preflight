"""Compact operational site-profile contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FieldStatus = Literal[
    "documented",
    "measured",
    "pilot_validated",
    "conflicting",
    "requires_probe",
    "requires_user_input",
    "requires_admin_confirmation",
    "not_found_after_bounded_search",
    "not_applicable",
]


class ProvenanceRef(BaseModel):
    """Reference from a compact field to detailed evidence."""

    model_config = ConfigDict(extra="forbid")

    source_type: str
    source_id: str
    observed_at: datetime | None = None


class ProfileValue(BaseModel):
    """One typed site-profile value with state and provenance."""

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    status: FieldStatus
    provenance: list[ProvenanceRef] = Field(default_factory=list)


class SubmissionOption(BaseModel):
    """Machine-actionable scheduler option syntax."""

    model_config = ConfigDict(extra="forbid")

    name: str
    syntax: str
    alternative_syntax: str | None = None
    requirement: Literal["required", "recommended", "optional", "conditional"]
    value: str | int | bool | None = None
    example: str | None = None
    allowed_values: list[str] = Field(default_factory=list)


class SiteProfile(BaseModel):
    """Compact candidate site profile consumed by the preflight planner."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    site_id: str
    site_name: str
    profile_state: Literal["candidate", "partial", "validated"] = "candidate"
    scheduler_type: str | None = None
    submission_options: list[SubmissionOption] = Field(default_factory=list)
    partitions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    resource_groups: dict[str, dict[str, Any]] = Field(default_factory=dict)
    storage: dict[str, ProfileValue] = Field(default_factory=dict)
    network: dict[str, ProfileValue] = Field(default_factory=dict)
