"""Compile measurement evidence into one minimal partial site profile."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import (
    EvidenceLink,
    EvidenceRecord,
    UnresolvedAction,
)
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.exceptions import ConfigurationError
from hpc_site_preflight.measurements.base import (
    MeasurementBundle,
    MeasurementObservation,
    MeasurementValue,
    SiteFacts,
)
from hpc_site_preflight.profiles.models import (
    AccountingProfile,
    FieldEvidenceLink,
    NetworkConnectionProfile,
    NetworkProfile,
    NodeNetworkProfile,
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

_STORAGE_PATH = re.compile(r"^/facts/storage/filesystems/([^/]+)/path$")
_STORAGE_ROLES = ("home", "project", "data", "scratch")


def compile_profile(
    measurements: MeasurementBundle,
) -> tuple[SiteProfile, EvidenceReport]:
    """Build an initial partial profile from login measurements."""

    if measurements.scheduler_type == "unknown":
        raise ConfigurationError("Cannot build a profile for an unknown scheduler.")

    site = measurements.site_facts
    observations = _measurement_observations(measurements)
    by_path = {item.path: item for item in observations}
    evidence = [_evidence_record(site.site_id, measurements, item) for item in observations]
    evidence_ids = {item.field_path: item.evidence_id for item in evidence}
    profile_links: list[FieldEvidenceLink] = []
    report_links: list[EvidenceLink] = []

    def link(profile_field: str, observation_path: str) -> None:
        evidence_id = evidence_ids.get(observation_path)
        if evidence_id is None:
            return
        profile_link = next(
            (item for item in profile_links if item.field == profile_field),
            None,
        )
        if profile_link is None:
            profile_links.append(
                FieldEvidenceLink(field=profile_field, evidence_ids=[evidence_id])
            )
        elif evidence_id not in profile_link.evidence_ids:
            profile_link.evidence_ids.append(evidence_id)

        report_link = next(
            (item for item in report_links if item.profile_field == profile_field),
            None,
        )
        if report_link is None:
            report_links.append(
                EvidenceLink(profile_field=profile_field, evidence_ids=[evidence_id])
            )
        elif evidence_id not in report_link.evidence_ids:
            report_link.evidence_ids.append(evidence_id)

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
    visible_accounts = _strings(by_path, "/facts/scheduler/visible_accounts") or []
    storage = _build_storage(by_path, visible_accounts, link)
    if visible_accounts:
        link("/accounting/visible_accounts", "/facts/scheduler/visible_accounts")

    software = SoftwareProfile(
        module_system=_string(by_path, "/facts/software/module_system"),
        workflow_tools=_strings(by_path, "/facts/software/workflow_tools") or [],
        container_runtimes=_strings(by_path, "/facts/software/container_runtimes") or [],
    )
    network = _build_network(site, by_path, link)
    submission_options = _submission_options(measurements.scheduler_type, partitions)
    unresolved = _unresolved_items(
        measurements.scheduler_type,
        submission_options,
        partitions,
        storage,
        network,
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
        schema_version="0.2",
        site_id=site.site_id,
        site_name=site.site_name,
        aliases=site.aliases,
        profile_state="partial",
        generated_at=measurements.collected_at,
        scheduler_type=measurements.scheduler_type,
        submit_command=submit_command,
        scheduler_version=scheduler_version,
        submission_options=submission_options,
        partitions=partitions,
        resource_groups=resource_groups,
        resource_shapes=resource_shapes,
        storage=storage,
        network=network,
        accounting=AccountingProfile(visible_accounts=visible_accounts),
        software=software,
        validation=_validation_states(
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


def _measurement_observations(
    bundle: MeasurementBundle,
) -> list[MeasurementObservation]:
    """Adapt structured measurements to the existing field-evidence compiler."""

    observations: list[MeasurementObservation] = []

    def add(
        path: str,
        value: MeasurementValue | None,
        command_id: str,
    ) -> None:
        if value is None:
            return
        observations.append(
            MeasurementObservation(
                path=path,
                status="observed",
                value=value,
                observed_at=bundle.collected_at,
                method="collector_function",
                command_id=command_id,
                source_reference="structured login-measurements.json",
            )
        )

    facts = bundle.site_facts
    add("/facts/identity/hostname", facts.hostname, "hostname_fqdn")
    add("/facts/identity/fqdn", facts.fqdn, "hostname_fqdn")
    add("/facts/identity/dns_suffix", facts.dns_suffix, "derive_dns_suffix")
    add("/facts/platform/os_id", facts.os_id, "read_os_release")
    add("/facts/platform/os_version", facts.os_version, "read_os_release")
    add("/facts/platform/kernel_release", facts.kernel_release, "kernel_release")
    add("/facts/platform/architecture", facts.architecture, "machine_architecture")
    add("/facts/user/username", facts.username, "getpass_getuser")
    add("/facts/user/uid", facts.uid, "os_getuid")
    add("/facts/user/groups", facts.groups, "get_group_names")
    add("/facts/user/home_directory", facts.home_directory, "HOME")
    add("/facts/user/working_directory", facts.working_directory, "os_getcwd")
    add("/facts/scheduler/detected_type", bundle.scheduler_type, "detect_scheduler")
    add(
        "/facts/scheduler/submit_command_available",
        bundle.submit_command_available,
        "find_submit_command",
    )

    for name, location in bundle.storage.items():
        prefix = f"/facts/storage/filesystems/{name}"
        add(f"{prefix}/path", location.observed_path, "discover_storage_environment")
        add(f"{prefix}/path_pattern", location.path_pattern, "derive_storage_path_pattern")
        add(f"{prefix}/filesystem_type", location.filesystem_type, "filesystem_type")
        add(f"{prefix}/readable", location.readable, "path_readable")
        add(f"{prefix}/writable", location.writable, "path_writable")
        add(f"{prefix}/available_bytes", location.available_bytes, "shutil_disk_usage")

    if bundle.networking:
        add(
            "/facts/networking/dns_resolution",
            bundle.networking.dns_resolution,
            "test_allowlisted_dns",
        )
        add(
            "/facts/networking/outbound_https_to_allowlisted_target",
            bundle.networking.outbound_https,
            "test_allowlisted_https",
        )
        add(
            "/facts/networking/local_tcp_bind",
            bundle.networking.local_tcp_bind,
            "test_local_tcp_bind",
        )
        add(
            "/facts/networking/local_tcp_loopback",
            bundle.networking.local_tcp_loopback,
            "test_tcp_loopback",
        )

    if bundle.slurm:
        add("/facts/scheduler/version", bundle.slurm.version, "slurm_version")
        add(
            "/facts/scheduler/partitions",
            [partition.name for partition in bundle.slurm.partitions],
            "slurm_partition_resources",
        )
        add(
            "/facts/scheduler/default_partition",
            bundle.slurm.default_partition,
            "slurm_partition_resources",
        )
        add(
            "/facts/scheduler/visible_accounts",
            bundle.slurm.visible_accounts,
            "slurm_associations",
        )
        for partition in bundle.slurm.partitions:
            prefix = f"/facts/scheduler/partitions/{partition.name}"
            add(f"{prefix}/available", partition.available, "slurm_partition_resources")
            add(
                f"{prefix}/visible_walltime_limit",
                partition.visible_walltime_limit,
                "slurm_partition_resources",
            )
            add(f"{prefix}/node_count", partition.node_count, "slurm_partition_resources")
            add(f"{prefix}/node_states", partition.node_states, "slurm_partition_resources")
            shape = f"/facts/scheduler/node_shapes/{partition.name}"
            add(f"{shape}/cpus", partition.cpus_per_node, "slurm_partition_resources")
            add(
                f"{shape}/memory_mib",
                partition.memory_mib_per_node,
                "slurm_partition_resources",
            )
            add(
                f"{shape}/gpu_count",
                partition.gpu_count_per_node,
                "slurm_partition_resources",
            )
            add(f"{shape}/gpu_models", partition.gpu_models, "slurm_partition_resources")
        add(
            "/facts/scheduler/node_shapes",
            [partition.name for partition in bundle.slurm.partitions],
            "slurm_partition_resources",
        )

    if bundle.htcondor:
        add("/facts/scheduler/version", bundle.htcondor.version, "htcondor_version")
        add(
            "/facts/scheduler/resource_groups",
            [group.key for group in bundle.htcondor.resource_groups],
            "htcondor_resource_groups",
        )
        for group in bundle.htcondor.resource_groups:
            prefix = f"/facts/scheduler/resource_groups/{group.key}"
            for field in (
                "machine_count",
                "slot_count",
                "cpus",
                "memory_mib",
                "disk_kib",
                "gpu_count",
            ):
                add(
                    f"{prefix}/{field}",
                    getattr(group, field),
                    "htcondor_resource_groups",
                )
    return observations


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
    observations: dict[str, MeasurementObservation],
    visible_accounts: list[str],
    link: Callable[[str, str], None],
) -> list[StorageProfile]:
    """Build common storage roles and generalize only measured path components."""

    observed_names = {
        match.group(1)
        for path in observations
        if (match := _STORAGE_PATH.match(path)) is not None
    }
    names = [*_STORAGE_ROLES, *sorted(observed_names - set(_STORAGE_ROLES))]
    username = _string(observations, "/facts/user/username")
    groups = _strings(observations, "/facts/user/groups") or []
    result: list[StorageProfile] = []
    for name in names:
        prefix = f"/facts/storage/filesystems/{name}"
        path = f"{prefix}/path"
        measured_path = _string(observations, path)
        pattern_path = f"{prefix}/path_pattern"
        measured_pattern = _string(observations, pattern_path)
        path_pattern = _path_pattern(
            measured_pattern or measured_path,
            username=username,
            accounts=visible_accounts,
            groups=groups,
        )
        result.append(
            StorageProfile(
                name=name,
                path_pattern=path_pattern,
                filesystem_type=_string(observations, f"{prefix}/filesystem_type"),
                login_readable=_boolean(observations, f"{prefix}/readable"),
                login_writable=_boolean(observations, f"{prefix}/writable"),
                available_bytes=_integer(observations, f"{prefix}/available_bytes"),
            )
        )
        if path_pattern is not None:
            link(
                f"/storage/{name}/path_pattern",
                pattern_path if _observed(observations, pattern_path) else path,
            )
            if path_pattern and "{username}" in path_pattern:
                link(f"/storage/{name}/path_pattern", "/facts/user/username")
            if path_pattern and "{account}" in path_pattern:
                link(
                    f"/storage/{name}/path_pattern",
                    "/facts/scheduler/visible_accounts",
                )
            if path_pattern and "{group}" in path_pattern:
                link(f"/storage/{name}/path_pattern", "/facts/user/groups")
        for field in ("filesystem_type", "readable", "writable", "available_bytes"):
            profile_field = {
                "readable": "login_readable",
                "writable": "login_writable",
            }.get(field, field)
            if _observed(observations, f"{prefix}/{field}") is not None:
                link(f"/storage/{name}/{profile_field}", f"{prefix}/{field}")
    return result


def _build_network(
    site: SiteFacts,
    observations: dict[str, MeasurementObservation],
    link: Callable[[str, str], None],
) -> NetworkProfile:
    """Build login networking from measurements and leave compute behavior unresolved."""

    measurement_paths = {
        "dns_resolution": "/facts/networking/dns_resolution",
        "outbound_https": "/facts/networking/outbound_https_to_allowlisted_target",
        "local_tcp_bind": "/facts/networking/local_tcp_bind",
        "local_tcp_loopback": "/facts/networking/local_tcp_loopback",
    }
    login_values = {
        field: _boolean(observations, path)
        for field, path in measurement_paths.items()
    }
    for field, path in measurement_paths.items():
        if login_values[field] is not None:
            link(f"/network/login/{field}", path)
    return NetworkProfile(
        login=NodeNetworkProfile(
            hostname_patterns=site.hostname_patterns,
            **login_values,
        ),
        compute=NodeNetworkProfile(),
        login_compute=NetworkConnectionProfile(),
        compute_compute=NetworkConnectionProfile(),
    )


def _path_pattern(
    path: str | None,
    *,
    username: str | None,
    accounts: list[str],
    groups: list[str],
) -> str | None:
    """Replace exact measured path components with supported profile placeholders."""

    if path is None:
        return None
    replacements = {group: "{group}" for group in groups}
    replacements.update({account: "{account}" for account in accounts})
    if username:
        replacements[username] = "{username}"
    return "/".join(replacements.get(component, component) for component in path.split("/"))


def _submission_options(
    scheduler: str, partitions: list[PartitionProfile]
) -> list[SubmissionOption]:
    if scheduler == "slurm":
        partition_names = [item.name for item in partitions]
        return [
            SubmissionOption(
                name="account",
                syntax=["-A {account}", "--account={account}"],
                required=None,
                example="<account>",
            ),
            SubmissionOption(
                name="partition",
                syntax=["-p {partition}", "--partition={partition}"],
                required=None,
                allowed_values=partition_names or None,
            ),
            SubmissionOption(
                name="nodes",
                syntax=["--nodes={count}", "-N {count}"],
                required=None,
                example="1",
            ),
            SubmissionOption(
                name="cpus-per-task",
                syntax=["--cpus-per-task={count}"],
                required=None,
                example="1",
            ),
            SubmissionOption(
                name="time",
                syntax=["-t {time}", "--time={time}"],
                required=None,
                example="01:00:00",
            ),
        ]
    return [
        SubmissionOption(
            name="request_cpus",
            syntax=["request_cpus = {count}"],
            required=None,
            example="1",
        ),
        SubmissionOption(
            name="request_memory",
            syntax=["request_memory = {memory_mib}MB"],
            required=None,
            example="1024MB",
        ),
        SubmissionOption(
            name="request_gpus",
            syntax=["request_gpus = {count}"],
            required=None,
            example="1",
        ),
    ]


def _unresolved_items(
    scheduler: str,
    submission_options: list[SubmissionOption],
    partitions: list[PartitionProfile],
    storage: list[StorageProfile],
    network: NetworkProfile,
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
    for option in submission_options:
        if option.required is None:
            items.append(
                UnresolvedWorkItem(
                    field=f"/submission_options/{option.name}/required",
                    reason="Login measurements do not establish whether this option is required.",
                    next_action="additional_documentation",
                    action_id="submission_policy_search",
                )
            )
    for field in (
        "dns_resolution",
        "outbound_https",
        "local_tcp_bind",
        "local_tcp_loopback",
    ):
        if getattr(network.login, field) is None:
            items.append(
                UnresolvedWorkItem(
                    field=f"/network/login/{field}",
                    reason="This login-node network fact was not measured.",
                    next_action="login_measurement",
                    action_id=f"login_{field}",
                )
            )
        items.append(
            UnresolvedWorkItem(
                field=f"/network/compute/{field}",
                reason="Compute-node network behavior requires approved evidence.",
                next_action="run_pilot",
                action_id=f"compute_{field}",
            )
        )
    for section in ("login_compute", "compute_compute"):
        for field in ("tcp_connect", "verified_tcp_port_range"):
            items.append(
                UnresolvedWorkItem(
                    field=f"/network/{section}/{field}",
                    reason="Cross-node TCP behavior requires approved evidence.",
                    next_action="run_pilot",
                    action_id=f"{section}_{field}",
                )
            )
    for item in storage:
        if item.path_pattern is None:
            items.append(
                UnresolvedWorkItem(
                    field=f"/storage/{item.name}/path_pattern",
                    reason="The storage role was not observed from the login node.",
                    next_action="login_measurement",
                    action_id=f"{item.name}_path",
                )
            )
        for field in ("compute_visible", "compute_readable", "compute_writable"):
            items.append(
                UnresolvedWorkItem(
                    field=f"/storage/{item.name}/{field}",
                    reason="Login-node access does not establish compute-node access.",
                    next_action="run_pilot",
                    action_id=f"{item.name}_{field}",
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


def _validation_states(*, resources: bool, storage: bool) -> list[SectionValidation]:
    return [
        SectionValidation(section="scheduler", state="measured"),
        SectionValidation(section="submission", state="partial"),
        SectionValidation(section="resources", state="measured" if resources else "partial"),
        SectionValidation(section="network", state="partial"),
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
