"""Compact operational site-profile contracts."""

from datetime import datetime
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

SchemaVersion = Literal["0.4"]
ProfileScalar: TypeAlias = str | int | float | bool
ProfileValue: TypeAlias = ProfileScalar | list[ProfileScalar]
ValidationState = Literal[
    "measured",
    "documented",
    "pilot_validated",
    "partial",
    "requires_pilot",
    "conflicting",
    "not_applicable",
]
NextAction = Literal[
    "login_measurement",
    "run_pilot",
    "additional_documentation",
    "user_input",
    "admin_confirmation",
]


class SubmissionOption(BaseModel):
    """One semantic scheduler option with ordered rendering forms."""

    model_config = ConfigDict(extra="forbid")

    name: str
    syntax: list[str] = Field(min_length=1)
    required: bool | None = None
    example: str | None = None
    allowed_values: list[str] | None = None


class UnmappedSubmissionOption(BaseModel):
    """One documented scheduler requirement awaiting a reviewed mapping."""

    model_config = ConfigDict(extra="forbid")

    documented_name: str
    documented_syntax: list[str] = Field(default_factory=list)
    requirement: Literal["required", "recommended", "optional", "conditional"]
    status: Literal["needs_mapping"] = "needs_mapping"


class PartitionProfile(BaseModel):
    """One visible Slurm partition and known normalized limits."""

    model_config = ConfigDict(extra="forbid")

    name: str
    maximum_walltime_seconds: int | None = Field(default=None, ge=-1)
    maximum_nodes_per_job: int | None = None
    shared_nodes: bool | None = None
    node_count: int | None = None
    cpus_per_node: int | None = None
    memory_mib_per_node: int | None = None
    temporary_disk_mib_per_node: int | None = None
    gpu_count_per_node: int | None = None
    gpu_models: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)


class HTCondorPoolTotalsProfile(BaseModel):
    """One timestamped snapshot of resources visible to HTCondor."""

    model_config = ConfigDict(extra="forbid")

    machine_count: int = 0
    cpu_cores: int = 0
    memory_mib: int = 0
    advertised_gpus: int = 0


class HTCondorCPUGroupProfile(BaseModel):
    """Visible machines grouped by advertised CPU cores."""

    model_config = ConfigDict(extra="forbid")

    cpu_cores_per_machine: int
    machine_count: int
    memory_mib_min: int | None = None
    memory_mib_max: int | None = None


class HTCondorGPUGroupProfile(BaseModel):
    """Visible GPU machines grouped by GPU count and CPU cores."""

    model_config = ConfigDict(extra="forbid")

    gpu_count_per_machine: int
    cpu_cores_per_machine: int
    machine_count: int
    memory_mib_min: int | None = None
    memory_mib_max: int | None = None


class SlurmProfile(BaseModel):
    """Slurm-specific submission and partition information."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    submit_command: str | None = None
    default_partition: str | None = None
    options: list[SubmissionOption] = Field(default_factory=list)
    unmapped_options: list[UnmappedSubmissionOption] = Field(default_factory=list)
    partitions: list[PartitionProfile] = Field(default_factory=list)


class HTCondorProfile(BaseModel):
    """HTCondor-specific submission and resource-group information."""

    model_config = ConfigDict(extra="forbid")

    version: str | None = None
    submit_command: str | None = None
    collector_host: str | None = None
    file_transfer_supported: bool | None = None
    guaranteed_runtime: bool | None = None
    preemptible: bool | None = None
    maximum_walltime: Literal["not_applicable"] = "not_applicable"
    submit_attributes: list[SubmissionOption] = Field(default_factory=list)
    unmapped_submit_attributes: list[UnmappedSubmissionOption] = Field(
        default_factory=list
    )
    pool_totals: HTCondorPoolTotalsProfile = Field(
        default_factory=HTCondorPoolTotalsProfile
    )
    cpu_groups: list[HTCondorCPUGroupProfile] = Field(default_factory=list)
    gpu_groups: list[HTCondorGPUGroupProfile] = Field(default_factory=list)


class StorageProfile(BaseModel):
    """One known storage path and unresolved compute behavior."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: str
    environment_variables: list[str] = Field(default_factory=list)
    path_pattern: str | None = None
    filesystem_type: str | None = None
    login_readable: bool | None = None
    login_writable: bool | None = None
    compute_visible: bool | None = None
    compute_readable: bool | None = None
    compute_writable: bool | None = None
    shared_across_compute_nodes: bool | None = None
    backup_policy: str | None = None
    purge_after_days: int | None = None
    purge_condition: str | None = None


class NodeNetworkProfile(BaseModel):
    """Networking facts for one node class."""

    model_config = ConfigDict(extra="forbid")

    hostname_patterns: list[str] = Field(default_factory=list)
    dns_resolution: bool | None = None
    outbound_https: bool | None = None
    local_tcp_bind: bool | None = None
    local_tcp_loopback: bool | None = None


class NetworkConnectionProfile(BaseModel):
    """TCP behavior between two node classes."""

    model_config = ConfigDict(extra="forbid")

    tcp_connect: bool | None = None
    verified_ports: list[int] | None = None
    suggested_port_range: str | None = None


class NetworkProfile(BaseModel):
    """Login, compute, and cross-node networking."""

    model_config = ConfigDict(extra="forbid")

    login: NodeNetworkProfile
    compute: NodeNetworkProfile
    login_compute: NetworkConnectionProfile
    compute_compute: NetworkConnectionProfile


class AccountingProfile(BaseModel):
    """Known accounting inputs and unresolved policy."""

    model_config = ConfigDict(extra="forbid")

    allocation_required: bool | None = None
    charging_unit: str | None = None
    charging_model: str | None = None
    filesystem_storage_charged: bool | None = None


class SectionStatus(BaseModel):
    """Compact validation state for each actionable profile section."""

    model_config = ConfigDict(extra="forbid")

    scheduler: ValidationState
    submission: ValidationState
    resources: ValidationState
    network: ValidationState
    storage: ValidationState
    accounting: ValidationState


class UnresolvedWorkItem(BaseModel):
    """One deterministic next action for an unresolved profile field."""

    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str
    next_action: NextAction
    action_id: str


class ConflictEvidenceValue(BaseModel):
    """One source and value participating in a profile conflict."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["measurement", "documentation", "pilot", "user"]
    value: ProfileValue | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ProfileConflict(BaseModel):
    """Compact note retaining a deterministic evidence conflict."""

    model_config = ConfigDict(extra="forbid")

    field: str
    selected_value: ProfileValue | None = None
    selected_evidence: str
    other_evidence: list[str] = Field(default_factory=list)
    evidence_values: list[ConflictEvidenceValue] = Field(default_factory=list)
    selection_rule: str
    note: str


class SiteProfile(BaseModel):
    """Compact actionable profile consumed by deterministic preflight."""

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion
    site_id: str
    site_name: str
    aliases: list[str] = Field(default_factory=list)
    profile_state: Literal["complete", "partial"]
    generated_at: datetime
    scheduler_type: Literal["slurm", "htcondor"]
    slurm: SlurmProfile | None = None
    htcondor: HTCondorProfile | None = None
    storage: list[StorageProfile] = Field(default_factory=list)
    network: NetworkProfile
    accounting: AccountingProfile
    section_status: SectionStatus
    unresolved: list[UnresolvedWorkItem] = Field(default_factory=list)
    conflicts: list[ProfileConflict] = Field(default_factory=list)
    evidence_id: str

    @model_validator(mode="after")
    def validate_scheduler_profile(self) -> Self:
        """Require only the scheduler-specific section selected by scheduler_type."""

        if self.scheduler_type == "slurm":
            if self.slurm is None or self.htcondor is not None:
                raise ValueError("Slurm profiles require slurm and forbid htcondor.")
        elif self.htcondor is None or self.slurm is not None:
            raise ValueError("HTCondor profiles require htcondor and forbid slurm.")
        return self
