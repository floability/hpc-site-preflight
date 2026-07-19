"""Normalized workflow requirements consumed by the preflight planner."""

from pydantic import BaseModel, ConfigDict, Field


class ResourceRequirements(BaseModel):
    """Minimal resource requirements for one portable workflow."""

    model_config = ConfigDict(extra="forbid")

    workers: int | None = None
    cores_per_worker: int | None = None
    memory_mb_per_worker: int | None = None
    gpus_per_worker: int | None = None
    walltime_seconds: int | None = None


class BackpackRequirements(BaseModel):
    """Normalized requirements extracted from a backpack."""

    model_config = ConfigDict(extra="forbid")

    backpack_id: str
    scheduler_backend: str | None = None
    resources: ResourceRequirements = Field(default_factory=ResourceRequirements)
    requires_shared_storage: bool | None = None
    requires_manager_worker_network: bool | None = None
    requires_worker_worker_network: bool | None = None
    requires_outbound_network: bool | None = None
