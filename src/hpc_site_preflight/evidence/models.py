"""Evidence-source and unresolved-action contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceMode = Literal["fixture", "live"]
FixtureOrigin = Literal["captured", "curated", "illustrative"]


class EvidenceItem(BaseModel):
    """One normalized claim or observation about the target site."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    field_path: str
    source_type: Literal["profile", "measurement", "documentation", "pilot", "user", "scheduler"]
    value: Any = None
    observed_at: datetime | None = None
    source_reference: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnresolvedAction(BaseModel):
    """The next safe action required for an unresolved field."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    action: Literal[
        "requires_probe",
        "requires_user_input",
        "requires_admin_confirmation",
        "requires_additional_documentation",
        "not_found_after_bounded_search",
    ]
    reason: str
