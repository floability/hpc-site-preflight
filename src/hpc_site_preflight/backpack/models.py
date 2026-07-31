"""Scheduler-independent workflow requirements consumed by preflight."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResourceRequirements(BaseModel):
    """Per-worker resources normalized from a Floability compute specification."""

    model_config = ConfigDict(extra="forbid")

    minimum_workers: int = Field(default=1, ge=0)
    maximum_workers: int = Field(default=5, ge=0)
    cores_per_worker: int = Field(default=1, ge=1)
    gpus_per_worker: int = Field(default=0, ge=0)
    memory_mb_per_worker: int | None = Field(default=None, ge=1)
    disk_mb_per_worker: int | None = Field(default=None, ge=1)
    walltime_seconds: int | None = Field(default=None, ge=1)


class BackpackRequirements(BaseModel):
    """Portable requirements plus the parsed Floability invocation."""

    model_config = ConfigDict(extra="forbid")

    backpack_id: str
    workflow_tool: Literal["floability"] = "floability"
    action: Literal["run", "execute", "workers start"]
    original_command: list[str] = Field(min_length=2)
    requested_scheduler: Literal["slurm", "condor"] | None = None
    batch_options: str | None = None
    condor_requirements: str | None = None
    manager_ports: list[int] = Field(default_factory=lambda: [9123, 9150])
    worker_transfer_port_range: str | None = None
    resources: ResourceRequirements = Field(default_factory=ResourceRequirements)
    requires_login_compute_network: bool = True
    requires_compute_compute_network: bool = False
