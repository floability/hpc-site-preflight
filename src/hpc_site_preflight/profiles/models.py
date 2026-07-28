"""Compact operational site-profile contracts."""

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

SchemaVersion = Literal["0.2"]
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
    value: ProfileScalar | None = None
    example: str | None = None
    allowed_values: list[str] | None = None


class PartitionProfile(BaseModel):
    """One visible Slurm partition and known normalized limits."""

    model_config = ConfigDict(extra="forbid")

    name: str
    available: bool | None = None
    visible_walltime_seconds: int | None = None
    maximum_walltime_seconds: int | None = None
    node_count: int | None = None


class ResourceGroupProfile(BaseModel):
    """One derived HTCondor resource group."""

    model_config = ConfigDict(extra="forbid")

    key: str
    machine_count: int | None = None
    slot_count: int | None = None
    cpus: int | None = None
    memory_mib: int | None = None
    disk_kib: int | None = None
    gpu_count: int | None = None


class ResourceShapeProfile(BaseModel):
    """One scheduler-visible resource shape."""

    model_config = ConfigDict(extra="forbid")

    key: str
    cpus: int | None = None
    memory_mib: int | None = None
    temporary_disk_mib: int | None = None
    gpu_count: int | None = None
    gpu_models: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)


class StorageProfile(BaseModel):
    """One known storage path and unresolved compute behavior."""

    model_config = ConfigDict(extra="forbid")

    name: str
    path_pattern: str | None = None
    filesystem_type: str | None = None
    login_readable: bool | None = None
    login_writable: bool | None = None
    compute_visible: bool | None = None
    compute_readable: bool | None = None
    compute_writable: bool | None = None
    available_bytes: int | None = None
    purge_after_days: int | None = None


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
    visible_accounts: list[str] = Field(default_factory=list)
    charging_model: str | None = None


class SoftwareProfile(BaseModel):
    """Login-visible software summary."""

    model_config = ConfigDict(extra="forbid")

    module_system: str | None = None
    workflow_tools: list[str] = Field(default_factory=list)
    container_runtimes: list[str] = Field(default_factory=list)


class SectionValidation(BaseModel):
    """Validation state for one compact profile section."""

    model_config = ConfigDict(extra="forbid")

    section: Literal[
        "scheduler",
        "submission",
        "resources",
        "network",
        "storage",
        "accounting",
        "software",
    ]
    state: ValidationState


class UnresolvedWorkItem(BaseModel):
    """One deterministic next action for an unresolved profile field."""

    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str
    next_action: NextAction
    action_id: str


class ProfileConflict(BaseModel):
    """Compact note retaining a deterministic evidence conflict."""

    model_config = ConfigDict(extra="forbid")

    field: str
    selected_value: ProfileValue | None = None
    selected_evidence: str
    other_evidence: list[str] = Field(default_factory=list)
    selection_rule: str
    note: str


class FieldEvidenceLink(BaseModel):
    """Compact links from one profile field to detailed evidence."""

    model_config = ConfigDict(extra="forbid")

    field: str
    evidence_ids: list[str] = Field(min_length=1)


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
    submit_command: str | None = None
    scheduler_version: str | None = None
    submission_options: list[SubmissionOption] = Field(default_factory=list)
    partitions: list[PartitionProfile] = Field(default_factory=list)
    resource_groups: list[ResourceGroupProfile] = Field(default_factory=list)
    resource_shapes: list[ResourceShapeProfile] = Field(default_factory=list)
    storage: list[StorageProfile] = Field(default_factory=list)
    network: NetworkProfile
    accounting: AccountingProfile
    software: SoftwareProfile
    validation: list[SectionValidation]
    unresolved: list[UnresolvedWorkItem] = Field(default_factory=list)
    conflicts: list[ProfileConflict] = Field(default_factory=list)
    evidence_report: str
    field_evidence: list[FieldEvidenceLink] = Field(default_factory=list)
