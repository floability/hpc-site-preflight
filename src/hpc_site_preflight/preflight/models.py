"""Execution-plan and early-failure result contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PreflightIssue(BaseModel):
    """One incompatibility, missing value, or unresolved requirement."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    severity: Literal["error", "unresolved", "warning"]
    reason: str
    remediation: str


class ExecutionPlan(BaseModel):
    """Site-specific settings generated for a compatible backpack."""

    model_config = ConfigDict(extra="forbid")

    scheduler_arguments: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class PreflightResult(BaseModel):
    """Either a ready execution plan or a blocked result."""

    model_config = ConfigDict(extra="forbid")

    result: Literal["ready", "blocked"]
    execution_plan: ExecutionPlan | None = None
    issues: list[PreflightIssue] = Field(default_factory=list)
