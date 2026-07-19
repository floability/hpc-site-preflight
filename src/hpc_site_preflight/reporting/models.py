"""Typed performance-report contracts."""

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StageStatus = Literal["running", "completed", "failed"]
RunStatus = Literal["running", "completed", "failed"]


class ModelUsage(BaseModel):
    """Aggregated model usage for a stage or whole run."""

    model_config = ConfigDict(extra="forbid")

    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    usage_available: bool = True


class StageMetrics(BaseModel):
    """Aggregated measurements for one named pipeline stage."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: StageStatus = "running"
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    model_usage: ModelUsage = Field(default_factory=ModelUsage)
    retries: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    error_type: str | None = None
    error_message: str | None = None


class ArtifactRecord(BaseModel):
    """One artifact created by the run."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    path: Path


class RunPerformance(BaseModel):
    """Complete aggregated report for one CLI command."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "0.1"
    run_id: str
    command: str
    mode: str | None = None
    status: RunStatus = "running"
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float | None = None
    model_usage: ModelUsage = Field(default_factory=ModelUsage)
    retries: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    steps: list[StageMetrics] = Field(default_factory=list)
    artifacts: list[ArtifactRecord] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
