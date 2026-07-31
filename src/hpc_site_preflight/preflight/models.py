"""Structured preflight decisions and execution plans."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.backpack.models import BackpackRequirements


class PreflightIssue(BaseModel):
    """One proven incompatibility, unresolved requirement, or warning."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    severity: Literal["error", "unresolved", "warning"]
    reason: str
    required_value: Any = None
    site_value: Any = None
    remediation: str


class ResourceSelection(BaseModel):
    """The Slurm partition or HTCondor resource group selected for workers."""

    model_config = ConfigDict(extra="forbid")

    scheduler: Literal["slurm", "htcondor"]
    name: str
    profile_path: str


class ExecutionPlan(BaseModel):
    """Site-specific Floability command generated for a compatible workflow."""

    model_config = ConfigDict(extra="forbid")

    selected_resource: ResourceSelection
    scheduler_arguments: list[str] = Field(default_factory=list)
    floability_command: list[str] = Field(default_factory=list)
    floability_command_text: str
    settings: dict[str, Any] = Field(default_factory=dict)


class PreflightNarration(BaseModel):
    """Optional model-written explanation that cannot change the decision."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)


class PreflightResult(BaseModel):
    """Authoritative deterministic outcome plus optional narration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1"] = "0.1"
    site_id: str
    workflow_id: str
    result: Literal["ready", "blocked", "unknown"]
    requirements: BackpackRequirements
    execution_plan: ExecutionPlan | None = None
    issues: list[PreflightIssue] = Field(default_factory=list)
    narrative: str | None = None
