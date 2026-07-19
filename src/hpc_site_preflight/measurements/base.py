"""Provider-neutral measurement interfaces and result contracts."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from hpc_site_preflight.evidence.models import EvidenceMode, FixtureOrigin
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo

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
MeasurementValue: TypeAlias = MeasurementScalar | list[MeasurementScalar]
SchemaVersion = Literal["0.1"]

COMMON_SCHEDULER_PATHS = {
    "/facts/scheduler/detected_type",
    "/facts/scheduler/version",
    "/facts/scheduler/available_commands",
    "/facts/scheduler/submit_command_available",
}
COMMON_PATH_PREFIXES = (
    "/facts/identity/",
    "/facts/platform/",
    "/facts/user/",
    "/facts/storage/",
    "/facts/software/",
    "/facts/networking/",
    "/facts/system_limits/",
)
SLURM_PATHS = {
    "/facts/scheduler/cluster_name",
    "/facts/scheduler/version",
    "/facts/scheduler/default_partition",
    "/facts/scheduler/partitions",
    "/facts/scheduler/node_states",
    "/facts/scheduler/node_shapes",
    "/facts/scheduler/visible_accounts",
    "/facts/scheduler/visible_qos",
    "/facts/scheduler/visible_association_limits",
    "/facts/scheduler/reservations",
    "/facts/scheduler/visible_configuration",
}
SLURM_PATH_PREFIXES = (
    "/facts/scheduler/partitions/",
    "/facts/scheduler/node_states/",
    "/facts/scheduler/node_shapes/",
)
HTCONDOR_PATHS = {
    "/facts/scheduler/collector_host",
    "/facts/scheduler/version",
    "/facts/scheduler/pool_version",
    "/facts/scheduler/collector_ads",
    "/facts/scheduler/collector_names",
    "/facts/scheduler/schedd_ads",
    "/facts/scheduler/schedd_names",
    "/facts/scheduler/submit_command_available",
    "/facts/scheduler/submission_client_version",
    "/facts/scheduler/raw_slot_classads",
    "/facts/scheduler/execute_machines",
    "/facts/scheduler/execute_machine_count",
    "/facts/scheduler/slot_count",
    "/facts/scheduler/static_slot_count",
    "/facts/scheduler/partitionable_slot_count",
    "/facts/scheduler/dynamic_slot_count",
    "/facts/scheduler/resource_groups",
}
HTCONDOR_PATH_PREFIXES = (
    "/facts/scheduler/raw_slot_classads/",
    "/facts/scheduler/resource_groups/",
)


class MeasurementProvenance(BaseModel):
    """Flat acquisition provenance shared by every measurement."""

    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    method: AcquisitionMethod
    command_id: str
    source_reference: str | None = None


class MeasurementObservation(MeasurementProvenance):
    """One flat value and its acquisition provenance."""

    path: str
    status: ObservationStatus
    value: MeasurementValue | None = None

    @model_validator(mode="after")
    def validate_value_status(self) -> Self:
        """Keep missing values distinct from false, zero, and empty lists."""

        if self.status == "observed" and self.value is None:
            raise ValueError("observed measurements require a value.")
        if self.status != "observed" and self.value is not None:
            raise ValueError("unobserved measurements must use a null value.")
        return self


class CommonMeasurements(RootModel[list[MeasurementObservation]]):
    """Scheduler-independent observations from the common catalog."""

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        invalid = [item.path for item in self.root if not _is_common_path(item.path)]
        if invalid:
            raise ValueError(f"paths are not common measurements: {', '.join(invalid)}")
        return self


class SlurmMeasurements(RootModel[list[MeasurementObservation]]):
    """Observations defined by the Slurm measurement catalog."""

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        invalid = [
            item.path
            for item in self.root
            if not _matches_path(item.path, SLURM_PATHS, SLURM_PATH_PREFIXES)
        ]
        if invalid:
            raise ValueError(f"paths are not Slurm measurements: {', '.join(invalid)}")
        return self


class HTCondorMeasurements(RootModel[list[MeasurementObservation]]):
    """Observations defined by the HTCondor measurement catalog."""

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        invalid = [
            item.path
            for item in self.root
            if not _matches_path(item.path, HTCONDOR_PATHS, HTCONDOR_PATH_PREFIXES)
        ]
        if invalid:
            raise ValueError(f"paths are not HTCondor measurements: {', '.join(invalid)}")
        return self


class MeasurementBundle(BaseModel):
    """Normalized login-node and scheduler measurements."""

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion
    site_id: str
    scheduler_type: Literal["slurm", "htcondor", "unknown"]
    collected_at: datetime
    source_mode: EvidenceMode
    fixture_origin: FixtureOrigin | None = None
    collector_version: str
    common: list[MeasurementObservation] = Field(default_factory=list)
    scheduler: list[MeasurementObservation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_fixture_origin(self) -> Self:
        """Validate acquisition mode and scheduler-specific paths."""

        if self.source_mode == "fixture" and self.fixture_origin is None:
            raise ValueError("fixture_origin is required when source_mode is 'fixture'.")
        if self.source_mode == "live" and self.fixture_origin is not None:
            raise ValueError("fixture_origin must be omitted when source_mode is 'live'.")
        CommonMeasurements.model_validate(self.common)
        if self.scheduler_type == "slurm":
            SlurmMeasurements.model_validate(self.scheduler)
        elif self.scheduler_type == "htcondor":
            HTCondorMeasurements.model_validate(self.scheduler)
        elif self.scheduler:
            raise ValueError("unknown schedulers cannot contain scheduler-specific measurements.")
        return self


class MeasurementProvider(ABC):
    """Load or capture measurement evidence through one stable interface."""

    @abstractmethod
    def collect(self, site: SiteInfo, tracker: RunTracker) -> MeasurementBundle:
        """Return normalized measurements."""


def _is_common_path(path: str) -> bool:
    return (
        path in {"/observed_at", "/source_mode", "/fixture_origin"}
        or path in COMMON_SCHEDULER_PATHS
        or path.startswith(COMMON_PATH_PREFIXES)
    )


def _matches_path(path: str, exact: set[str], prefixes: tuple[str, ...]) -> bool:
    return path in exact or path.startswith(prefixes)
