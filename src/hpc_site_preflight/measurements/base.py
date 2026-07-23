"""Structured login-measurement contracts and provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hpc_site_preflight.evidence.models import EvidenceSource
from hpc_site_preflight.reporting.tracker import RunTracker

ObservationStatus = Literal[
    "observed",
    "unavailable",
    "hidden",
    "command_unavailable",
    "permission_denied",
    "not_applicable",
    "failed",
]
AcquisitionMethod = Literal[
    "python_api",
    "environment_variable",
    "fixed_command",
    "executable_lookup",
    "collector_function",
    "derived",
]
MeasurementScalar: TypeAlias = str | int | float | bool
MeasurementValue: TypeAlias = (
    MeasurementScalar | list[str] | list[int] | list[float] | list[bool]
)


class StrictModel(BaseModel):
    """Forbid unreviewed fields in external measurement JSON."""

    model_config = ConfigDict(extra="forbid")


class MeasurementProvenance(StrictModel):
    """Legacy per-field provenance retained for evidence compatibility."""

    observed_at: datetime
    method: AcquisitionMethod
    command_id: str
    source_reference: str | None = None


class MeasurementObservation(MeasurementProvenance):
    """Legacy flat observation accepted by evidence helper tests."""

    path: str
    status: ObservationStatus
    value: MeasurementValue | None = None

    @model_validator(mode="after")
    def validate_value_status(self) -> Self:
        if self.status == "observed" and self.value is None:
            raise ValueError("observed measurements require a value.")
        if self.status != "observed" and self.value is not None:
            raise ValueError("unobserved measurements must use a null value.")
        return self


class SiteFacts(StrictModel):
    """Site identity and low-cost login-node context."""

    site_id: str = Field(min_length=1)
    site_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    hostname: str | None = None
    fqdn: str | None = None
    dns_suffix: str | None = None
    hostname_patterns: list[str] = Field(default_factory=list)
    documentation_domains: list[str] = Field(default_factory=list)
    preferred_path_tokens: list[str] = Field(default_factory=list)
    username: str | None = None
    uid: int | None = None
    groups: list[str] = Field(default_factory=list)
    home_directory: str | None = None
    working_directory: str | None = None
    os_id: str | None = None
    os_version: str | None = None
    kernel_release: str | None = None
    architecture: str | None = None
    cpu_count: int | None = None
    available_memory_bytes: int | None = None


class StorageLocation(StrictModel):
    """One fixed common storage role."""

    source_environment_variable: str | None = None
    observed_path: str | None = None
    path_pattern: str | None = None
    exists: bool | None = None
    readable: bool | None = None
    writable: bool | None = None
    executable: bool | None = None
    permissions: str | None = None
    filesystem_type: str | None = None
    available_bytes: int | None = None


class StorageFacts(StrictModel):
    """The four storage roles deliberately searched by the collector."""

    home: StorageLocation
    tmp: StorageLocation
    scratch: StorageLocation
    project: StorageLocation

    def items(self) -> list[tuple[str, StorageLocation]]:
        return [
            ("home", self.home),
            ("tmp", self.tmp),
            ("scratch", self.scratch),
            ("project", self.project),
        ]


class SlurmPartition(StrictModel):
    """One scheduler-visible Slurm partition."""

    name: str
    node_count: int | None = None
    memory_mib_per_node: int | None = None
    cpus_per_node: int | None = None
    gres: list[str] = Field(default_factory=list)
    gpu_count_per_node: int | None = None
    gpu_models: list[str] = Field(default_factory=list)
    available: bool | None = None
    visible_walltime_limit: str | None = None
    node_states: list[str] = Field(default_factory=list)


class SlurmFacts(StrictModel):
    """Structured login-visible Slurm facts."""

    version: str | None = None
    available_commands: list[str] = Field(default_factory=list)
    submit_command_available: bool
    default_partition: str | None = None
    partitions: list[SlurmPartition] = Field(default_factory=list)
    visible_accounts: list[str] = Field(default_factory=list)
    visible_qos: list[str] = Field(default_factory=list)


class HTCondorResourceGroup(StrictModel):
    """One observable HTCondor resource group."""

    key: str
    machine_count: int | None = None
    slot_count: int | None = None
    cpus: int | None = None
    memory_mib: int | None = None
    disk_kib: int | None = None
    gpu_count: int | None = None


class HTCondorFacts(StrictModel):
    """Structured login-visible HTCondor facts implemented so far."""

    available_commands: list[str] = Field(default_factory=list)
    submit_command_available: bool
    version: str | None = None
    collector_host: str | None = None
    resource_groups: list[HTCondorResourceGroup] = Field(default_factory=list)


class LoginNetworking(StrictModel):
    """Optional login-node networking values retained for profile construction."""

    dns_resolution: bool | None = None
    outbound_https: bool | None = None
    local_tcp_bind: bool | None = None
    local_tcp_loopback: bool | None = None


class MeasurementBundle(StrictModel):
    """The single external site identity and login-measurement document."""

    schema_version: Literal["0.6"]
    collected_at: datetime
    evidence_source: EvidenceSource
    collector_version: str
    detected_schedulers: list[Literal["slurm", "htcondor"]]
    site_facts: SiteFacts
    storage: StorageFacts
    slurm: SlurmFacts | None
    htcondor: HTCondorFacts | None
    networking: LoginNetworking | None = None

    @model_validator(mode="after")
    def validate_schedulers(self) -> Self:
        """Keep scheduler selection consistent with nullable scheduler objects."""

        if ("slurm" in self.detected_schedulers) != (self.slurm is not None):
            raise ValueError("detected_schedulers and slurm must agree.")
        if ("htcondor" in self.detected_schedulers) != (self.htcondor is not None):
            raise ValueError("detected_schedulers and htcondor must agree.")
        if len(set(self.detected_schedulers)) != len(self.detected_schedulers):
            raise ValueError("detected_schedulers cannot contain duplicates.")
        return self

    @property
    def site_id(self) -> str:
        return self.site_facts.site_id

    @property
    def scheduler_type(self) -> Literal["slurm", "htcondor", "unknown"]:
        if "slurm" in self.detected_schedulers:
            return "slurm"
        if "htcondor" in self.detected_schedulers:
            return "htcondor"
        return "unknown"

    @property
    def scheduler_version(self) -> str | None:
        if self.scheduler_type == "slurm" and self.slurm:
            return self.slurm.version
        if self.scheduler_type == "htcondor" and self.htcondor:
            return self.htcondor.version
        return None

    @property
    def submit_command_available(self) -> bool:
        if self.scheduler_type == "slurm" and self.slurm:
            return self.slurm.submit_command_available
        if self.scheduler_type == "htcondor" and self.htcondor:
            return self.htcondor.submit_command_available
        return False

    @property
    def storage_names(self) -> set[str]:
        return {
            name
            for name, location in self.storage.items()
            if location.observed_path is not None
        }

    @property
    def partition_names(self) -> set[str]:
        return {item.name for item in self.slurm.partitions} if self.slurm else set()

class MeasurementProvider(ABC):
    """Load or capture login measurements through one stable interface."""

    @abstractmethod
    def collect(self, tracker: RunTracker) -> MeasurementBundle:
        """Return validated structured measurements."""
