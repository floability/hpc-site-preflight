"""Scheduler-neutral approved-pilot inputs, results, and interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hpc_site_preflight.evidence.models import EvidenceSource
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.reporting.tracker import RunTracker


class StoragePilotResult(BaseModel):
    """Compute-node access observed for one login-visible storage path."""

    model_config = ConfigDict(extra="ignore")

    name: str
    path: str
    visible: bool | None = None
    readable: bool | None = None
    writable: bool | None = None


class PortAttempt(BaseModel):
    """One completed authenticated TCP connection attempt."""

    model_config = ConfigDict(extra="ignore")

    port: int = Field(ge=1024, le=65535)
    status: Literal["passed", "failed"]
    error: str | None = None


class PilotResultBundle(BaseModel):
    """Flat partial result accepted from live or recorded pilots."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["0.1"] = "0.1"
    site_id: str
    scheduler: Literal["slurm", "htcondor"]
    evidence_source: EvidenceSource
    pilot_id: str
    collected_at: datetime | None = None
    status: Literal["completed", "partial", "failed"] = "partial"
    storage_results: list[StoragePilotResult] | None = None
    login_compute_attempts: list[PortAttempt] | None = None
    compute_compute_attempts: list[PortAttempt] | None = None
    primary_node: str | None = None
    peer_node: str | None = None
    distinct_nodes: bool | None = None
    errors: list[str] = Field(default_factory=list)
    source_reference: str | None = None


class PilotInputs(BaseModel):
    """Values needed to run a scheduler pilot without a measurement bundle."""

    model_config = ConfigDict(extra="forbid")

    site_id: str
    scheduler: Literal["slurm", "htcondor"]
    login_host: str
    storage: dict[str, str] = Field(default_factory=dict)
    output: Path
    runs_dir: Path
    start_port: int = Field(default=9000, ge=1024, le=61535)
    port_count: int = Field(default=8, ge=2, le=32)
    coordination_timeout: float = Field(default=3600.0, gt=0)
    partition: str | None = None
    account: str | None = None

    @model_validator(mode="after")
    def validate_port_plan(self) -> PilotInputs:
        """Keep the bounded test at two ports per 1,000-port block."""

        if self.port_count % 2:
            raise ValueError("port_count must be even")
        blocks = self.port_count // 2
        if self.start_port + blocks * 1000 - 1 > 65535:
            raise ValueError("pilot ports must remain at or below 65535")
        return self


class PilotRunner(ABC):
    """Run one scheduler-specific approved pilot."""

    @abstractmethod
    def run(self, inputs: PilotInputs) -> PilotResultBundle:
        """Return a flat result even when only part of the pilot completes."""


class PilotProvider(ABC):
    """Load or run only predefined bounded pilots."""

    @abstractmethod
    def collect(
        self, measurements: MeasurementBundle, tracker: RunTracker
    ) -> PilotResultBundle:
        """Return normalized pilot results."""
