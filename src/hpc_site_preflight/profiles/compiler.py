"""Compile measurement evidence into one minimal partial site profile."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from hpc_site_preflight.documentation.models import DocumentationEvidence
from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import (
    EvidenceLink,
    EvidenceRecord,
    UnresolvedAction,
)
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.exceptions import ConfigurationError
from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementObservation
from hpc_site_preflight.profiles.documentation import apply_documentation
from hpc_site_preflight.profiles.models import (
    AccountingProfile,
    FieldEvidenceLink,
    NetworkCapability,
    PartitionProfile,
    ResourceGroupProfile,
    ResourceShapeProfile,
    SectionValidation,
    SiteProfile,
    SoftwareProfile,
    StorageProfile,
    SubmissionOption,
    UnresolvedWorkItem,
)
from hpc_site_preflight.site_descriptor.models import SiteDescriptor

_STORAGE_PATH = re.compile(r"^/facts/storage/filesystems/([^/]+)/path$")


def compile_profile(
    site: SiteDescriptor,
    measurements: MeasurementBundle,
    documentation: DocumentationEvidence | None = None,
) -> tuple[SiteProfile, EvidenceReport]:
    """Build a partial profile from measurements and optional documentation."""

    if site.site_id != measurements.site_id or site.scheduler != measurements.scheduler_type:
        raise ConfigurationError("Site descriptor and measurements do not identify the same site.")
    if measurements.scheduler_type == "unknown":
        raise ConfigurationError("Cannot build a profile for an unknown scheduler.")

    observations = [*measurements.common, *measurements.scheduler]
    by_path = {item.path: item for item in observations}
    evidence = [_evidence_record(site.site_id, measurements, item) for item in observations]
    evidence_ids = {item.field_path: item.evidence_id for item in evidence}
    profile_links: list[FieldEvidenceLink] = []
    report_links: list[EvidenceLink] = []

    def link(profile_field: str, observation_path: str) -> None:
        evidence_id = evidence_ids.get(observation_path)
        if evidence_id is None:
            return
        profile_links.append(FieldEvidenceLink(field=profile_field, evidence_ids=[evidence_id]))
        report_links.append(
            EvidenceLink(profile_field=profile_field, evidence_ids=[evidence_id])
        )

    scheduler_version = _string(by_path, "/facts/scheduler/version")
    link("/scheduler_type", "/facts/scheduler/detected_type")
    if scheduler_version is not None:
        link("/scheduler_version", "/facts/scheduler/version")

    submit_available = _boolean(by_path, "/facts/scheduler/submit_command_available")
    submit_command = None
    if submit_available is True:
        submit_command = "sbatch" if measurements.scheduler_type == "slurm" else "condor_submit"
        link("/submit_command", "/facts/scheduler/submit_command_available")

    partitions = _build_partitions(by_path, link)
    resource_groups = _build_resource_groups(by_path, link)
    resource_shapes = _build_resource_shapes(by_path, link)
    storage = _build_storage(by_path, link)
    visible_accounts = _strings(by_path, "/facts/scheduler/visible_accounts") or []
    if visible_accounts:
        link("/accounting/visible_accounts", "/facts/scheduler/visible_accounts")

    software = SoftwareProfile(
        module_system=_string(by_path, "/facts/software/module_system"),
        workflow_tools=_strings(by_path, "/facts/software/workflow_tools") or [],
        container_runtimes=_strings(by_path, "/facts/software/container_runtimes") or [],
    )
    network = [
        NetworkCapability(name="manager_worker", available=None),
        NetworkCapability(name="worker_worker", available=None),
        NetworkCapability(name="outbound_compute", available=None),
    ]
    unresolved = _unresolved_items(
        measurements.scheduler_type,
        partitions,
        storage,
        submit_command,
    )
    report_unresolved = [
        UnresolvedAction(
            field_path=item.field,
            action=item.next_action,
            action_id=item.action_id,
            reason=item.reason,
        )
        for item in unresolved
    ]

    profile = SiteProfile(
        schema_version="0.1",
        site_id=site.site_id,
        site_name=site.site_name,
        aliases=site.aliases,
        profile_state="partial",
        generated_at=measurements.collected_at,
        scheduler_type=measurements.scheduler_type,
        submit_command=submit_command,
        scheduler_version=scheduler_version,
        submission_options=_submission_options(measurements.scheduler_type, partitions),
        partitions=partitions,
        resource_groups=resource_groups,
        resource_shapes=resource_shapes,
        storage=storage,
        network=network,
        accounting=AccountingProfile(visible_accounts=visible_accounts),
        software=software,
        validation=_validation_states(
            submit_command=submit_command,
            resources=bool(partitions or resource_groups or resource_shapes),
            storage=bool(storage),
        ),
        unresolved=unresolved,
        conflicts=[],
        evidence_report="evidence-report.json",
        field_evidence=profile_links,
    )
    report_id = build_evidence_id(
        "report", site.site_id, "/", measurements.collected_at.isoformat()
    )
    report = EvidenceReport(
        schema_version="0.1",
        report_id=report_id,
        site_id=site.site_id,
        generated_at=measurements.collected_at,
        evidence=evidence,
        conflicts=[],
        links=report_links,
        unresolved=report_unresolved,
    )
    if documentation is not None:
        return apply_documentation(profile, report, documentation)
    return profile, report


def _evidence_record(
    site_id: str, bundle: MeasurementBundle, observation: MeasurementObservation
) -> EvidenceRecord:
    trust: Literal["illustrative", "captured"] = (
        "illustrative" if bundle.evidence_source == "simulated" else "captured"
    )
    source_key = f"{observation.observed_at.isoformat()}:{observation.command_id}"
    evidence_id = build_evidence_id("measurement", site_id, observation.path, source_key)
    accepted = observation.status == "observed"
    return EvidenceRecord(
        evidence_id=evidence_id,
        field_path=observation.path,
        source_type="measurement",
        scope="target_site",
        trust=trust,
        disposition="accepted" if accepted else "invalid",
        value=observation.value if accepted else None,
        observed_at=observation.observed_at,
        freshness="per_run",
        source_reference=observation.source_reference,
        command_id=observation.command_id,
        reason=None if accepted else observation.status,
    )


def _build_partitions(
    observations: dict[str, MeasurementObservation], link: Callable[[str, str], None]
) -> list[PartitionProfile]:
    names = _strings(observations, "/facts/scheduler/partitions") or []
    result: list[PartitionProfile] = []
    for name in names:
        prefix = f"/facts/scheduler/partitions/{name}"
        visible_path = f"{prefix}/visible_walltime_limit"
        visible_seconds = _duration_seconds(_string(observations, visible_path))
        result.append(
            PartitionProfile(
                name=name,
                available=_boolean(observations, f"{prefix}/available"),
                visible_walltime_seconds=visible_seconds,
                maximum_walltime_seconds=None,
                node_count=_integer(observations, f"{prefix}/node_count"),
            )
        )
        link(f"/partitions/{name}/available", f"{prefix}/available")
        link(f"/partitions/{name}/visible_walltime_seconds", visible_path)
    link("/partitions", "/facts/scheduler/partitions")
    return result


def _build_resource_shapes(
    observations: dict[str, MeasurementObservation], link: Callable[[str, str], None]
) -> list[ResourceShapeProfile]:
    names = _strings(observations, "/facts/scheduler/node_shapes") or []
    result: list[ResourceShapeProfile] = []
    for name in names:
        prefix = f"/facts/scheduler/node_shapes/{name}"
        result.append(
            ResourceShapeProfile(
                key=name,
                cpus=_integer(observations, f"{prefix}/cpus"),
                memory_mib=_integer(observations, f"{prefix}/memory_mib"),
                temporary_disk_mib=_integer(observations, f"{prefix}/temporary_disk_mib"),
                gpu_count=_integer(observations, f"{prefix}/gpu_count"),
                gpu_models=_strings(observations, f"{prefix}/gpu_models") or [],
                features=_strings(observations, f"{prefix}/features") or [],
            )
        )
        for field in ("cpus", "memory_mib", "temporary_disk_mib", "gpu_count", "gpu_models"):
            link(f"/resource_shapes/{name}/{field}", f"{prefix}/{field}")
    link("/resource_shapes", "/facts/scheduler/node_shapes")
    return result


def _build_resource_groups(
    observations: dict[str, MeasurementObservation], link: Callable[[str, str], None]
) -> list[ResourceGroupProfile]:
    names = _strings(observations, "/facts/scheduler/resource_groups") or []
    result: list[ResourceGroupProfile] = []
    for name in names:
        prefix = f"/facts/scheduler/resource_groups/{name}"
        result.append(
            ResourceGroupProfile(
                key=_string(observations, f"{prefix}/key") or name,
                machine_count=_integer(observations, f"{prefix}/machine_count"),
                slot_count=_integer(observations, f"{prefix}/slot_count"),
            )
        )
        for field in ("key", "machine_count", "slot_count"):
            link(f"/resource_groups/{name}/{field}", f"{prefix}/{field}")
    link("/resource_groups", "/facts/scheduler/resource_groups")
    return result


def _build_storage(
    observations: dict[str, MeasurementObservation], link: Callable[[str, str], None]
) -> list[StorageProfile]:
    result: list[StorageProfile] = []
    for path in sorted(observations):
        match = _STORAGE_PATH.match(path)
        if match is None:
            continue
        name = match.group(1)
        prefix = f"/facts/storage/filesystems/{name}"
        result.append(
            StorageProfile(
                name=name,
                path=_string(observations, path),
                login_readable=_boolean(observations, f"{prefix}/readable"),
                login_writable=_boolean(observations, f"{prefix}/writable"),
                available_bytes=_integer(observations, f"{prefix}/available_bytes"),
            )
        )
        link(f"/storage/{name}/path", path)
    return result


def _submission_options(
    scheduler: str, partitions: list[PartitionProfile]
) -> list[SubmissionOption]:
    if scheduler == "slurm":
        partition_names = [item.name for item in partitions]
        return [
            SubmissionOption(
                name="account",
                syntax=["-A {account}", "--account={account}"],
                requirement="required",
                example="<account>",
            ),
            SubmissionOption(
                name="partition",
                syntax=["-p {partition}", "--partition={partition}"],
                requirement="required",
                allowed_values=partition_names or None,
            ),
            SubmissionOption(
                name="nodes",
                syntax=["--nodes={count}", "-N {count}"],
                requirement="required",
                example="1",
            ),
            SubmissionOption(
                name="cpus-per-task",
                syntax=["--cpus-per-task={count}"],
                requirement="required",
                example="1",
            ),
            SubmissionOption(
                name="time",
                syntax=["-t {time}", "--time={time}"],
                requirement="required",
                example="01:00:00",
            ),
        ]
    return [
        SubmissionOption(
            name="request_cpus",
            syntax=["request_cpus = {count}"],
            requirement="required",
            example="1",
        ),
        SubmissionOption(
            name="request_memory",
            syntax=["request_memory = {memory_mib}MB"],
            requirement="required",
            example="1024MB",
        ),
        SubmissionOption(
            name="request_gpus",
            syntax=["request_gpus = {count}"],
            requirement="conditional",
            example="1",
        ),
    ]


def _unresolved_items(
    scheduler: str,
    partitions: list[PartitionProfile],
    storage: list[StorageProfile],
    submit_command: str | None,
) -> list[UnresolvedWorkItem]:
    items: list[UnresolvedWorkItem] = []
    if submit_command is None:
        items.append(
            UnresolvedWorkItem(
                field="/submit_command",
                reason="The submit executable was not observed as available.",
                next_action="user_input",
                action_id="submit_command_input",
            )
        )
    if scheduler == "slurm":
        for partition in partitions:
            items.append(
                UnresolvedWorkItem(
                    field=f"/partitions/{partition.name}/maximum_walltime_seconds",
                    reason="Visible scheduler configuration does not establish enforced policy.",
                    next_action="additional_documentation",
                    action_id="partition_policy_search",
                )
            )
    for capability in ("manager_worker", "worker_worker", "outbound_compute"):
        items.append(
            UnresolvedWorkItem(
                field=f"/network/{capability}",
                reason="Compute-node network behavior requires approved evidence.",
                next_action="run_pilot",
                action_id=f"{capability}_check",
            )
        )
    for item in storage:
        items.append(
            UnresolvedWorkItem(
                field=f"/storage/{item.name}/compute_visible",
                reason="Login-node visibility does not establish compute-node visibility.",
                next_action="run_pilot",
                action_id="shared_storage_visibility",
            )
        )
    items.append(
        UnresolvedWorkItem(
            field="/accounting/charging_model",
            reason="Charging policy cannot be measured from the login node.",
            next_action="additional_documentation",
            action_id="accounting_policy_search",
        )
    )
    return items


def _validation_states(
    *, submit_command: str | None, resources: bool, storage: bool
) -> list[SectionValidation]:
    return [
        SectionValidation(section="scheduler", state="measured"),
        SectionValidation(
            section="submission", state="measured" if submit_command else "partial"
        ),
        SectionValidation(section="resources", state="measured" if resources else "partial"),
        SectionValidation(section="network", state="requires_pilot"),
        SectionValidation(section="storage", state="partial" if storage else "requires_pilot"),
        SectionValidation(section="accounting", state="partial"),
        SectionValidation(section="software", state="partial"),
    ]


def _observed(
    observations: dict[str, MeasurementObservation], path: str
) -> MeasurementObservation | None:
    item = observations.get(path)
    return item if item is not None and item.status == "observed" else None


def _string(observations: dict[str, MeasurementObservation], path: str) -> str | None:
    item = _observed(observations, path)
    return item.value if item is not None and isinstance(item.value, str) else None


def _integer(observations: dict[str, MeasurementObservation], path: str) -> int | None:
    item = _observed(observations, path)
    value = item.value if item is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _boolean(observations: dict[str, MeasurementObservation], path: str) -> bool | None:
    item = _observed(observations, path)
    return item.value if item is not None and isinstance(item.value, bool) else None


def _strings(
    observations: dict[str, MeasurementObservation], path: str
) -> list[str] | None:
    item = _observed(observations, path)
    if item is None or not isinstance(item.value, list):
        return None
    return [value for value in item.value if isinstance(value, str)]


def _duration_seconds(value: str | None) -> int | None:
    if value is None or value.lower() in {"infinite", "unlimited"}:
        return None
    day_parts = value.split("-", maxsplit=1)
    days = int(day_parts[0]) if len(day_parts) == 2 and day_parts[0].isdigit() else 0
    clock = day_parts[-1].split(":")
    if len(clock) != 3 or not all(part.isdigit() for part in clock):
        return None
    hours, minutes, seconds = (int(part) for part in clock)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds
