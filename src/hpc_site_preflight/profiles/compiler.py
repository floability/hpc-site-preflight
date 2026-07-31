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
    HTCondorCPUGroupProfile,
    HTCondorGPUGroupProfile,
    HTCondorPoolTotalsProfile,
    HTCondorProfile,
    NetworkConnectionProfile,
    NetworkProfile,
    NodeNetworkProfile,
    PartitionProfile,
    SectionStatus,
    SiteProfile,
    SlurmProfile,
    StorageProfile,
    SubmissionOption,
    UnresolvedWorkItem,
)

_STORAGE_PATH = re.compile(r"^/facts/storage/filesystems/([^/]+)/path$")


def compile_profile(
    measurements: MeasurementBundle,
    *,
    evidence_id: str | None = None,
) -> tuple[SiteProfile, EvidenceReport]:
    """Build an initial partial profile from login measurements."""

    if measurements.scheduler_type == "unknown":
        raise ConfigurationError("Cannot build a profile for an unknown scheduler.")

    site = measurements.site_facts
    observations = _measurement_observations(measurements)
    by_path = {item.path: item for item in observations}
    evidence = [_evidence_record(site.site_id, measurements, item) for item in observations]
    evidence_ids = {item.field_path: item.evidence_id for item in evidence}
    report_links: list[EvidenceLink] = []

    def link(profile_field: str, observation_path: str) -> None:
        evidence_id = evidence_ids.get(observation_path)
        if evidence_id is None:
            return
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
        link(
            f"/{measurements.scheduler_type}/version",
            "/facts/scheduler/version",
        )
    if measurements.slurm and measurements.slurm.default_partition is not None:
        link("/slurm/default_partition", "/facts/scheduler/default_partition")
    if measurements.htcondor and measurements.htcondor.collector_host is not None:
        link(
            "/htcondor/collector_host",
            "/facts/scheduler/htcondor/collector_host",
        )
    if (
        measurements.htcondor
        and measurements.htcondor.file_transfer_supported is not None
    ):
        link(
            "/htcondor/file_transfer_supported",
            "/facts/scheduler/htcondor/file_transfer_supported",
        )

    submit_available = _boolean(by_path, "/facts/scheduler/submit_command_available")
    submit_command = None
    if submit_available is True:
        submit_command = "sbatch" if measurements.scheduler_type == "slurm" else "condor_submit"
        link(
            f"/{measurements.scheduler_type}/submit_command",
            "/facts/scheduler/submit_command_available",
        )

    partitions = _build_partitions(by_path, link)
    pool_totals, cpu_groups, gpu_groups = _build_htcondor_resources(by_path, link)
    storage = _build_storage(by_path, link)
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

    report_id = evidence_id or build_evidence_id(
        "report", site.site_id, "/", measurements.collected_at.isoformat()
    ).replace(":", "-")
    profile = SiteProfile(
        schema_version="0.4",
        site_id=site.site_id,
        site_name=site.site_name,
        aliases=site.aliases,
        profile_state="partial",
        generated_at=measurements.collected_at,
        scheduler_type=measurements.scheduler_type,
        slurm=(
            SlurmProfile(
                version=scheduler_version,
                submit_command=submit_command,
                default_partition=(
                    measurements.slurm.default_partition if measurements.slurm else None
                ),
                options=submission_options,
                partitions=partitions,
            )
            if measurements.scheduler_type == "slurm"
            else None
        ),
        htcondor=(
            HTCondorProfile(
                version=scheduler_version,
                submit_command=submit_command,
                collector_host=(
                    measurements.htcondor.collector_host if measurements.htcondor else None
                ),
                file_transfer_supported=(
                    measurements.htcondor.file_transfer_supported
                    if measurements.htcondor
                    else None
                ),
                submit_attributes=submission_options,
                pool_totals=pool_totals,
                cpu_groups=cpu_groups,
                gpu_groups=gpu_groups,
            )
            if measurements.scheduler_type == "htcondor"
            else None
        ),
        storage=storage,
        network=network,
        accounting=AccountingProfile(),
        section_status=_section_status(
            resources=bool(partitions or cpu_groups),
            storage=bool(storage),
        ),
        unresolved=unresolved,
        conflicts=[],
        evidence_id=report_id,
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
        add(f"{prefix}/id", location.id, "discover_storage")
        add(f"{prefix}/role", location.role, "discover_storage")
        add(
            f"{prefix}/environment_variables",
            location.environment_variables,
            "discover_storage_environment",
        )
        add(f"{prefix}/path", location.observed_path, "discover_storage_environment")
        add(f"{prefix}/path_pattern", location.path_pattern, "derive_storage_path_pattern")
        add(f"{prefix}/filesystem_type", location.filesystem_type, "filesystem_type")
        add(f"{prefix}/readable", location.readable, "path_readable")
        add(f"{prefix}/writable", location.writable, "path_writable")

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
        for partition in bundle.slurm.partitions:
            prefix = f"/facts/scheduler/partitions/{partition.name}"
            add(
                f"{prefix}/maximum_walltime_seconds",
                partition.maximum_walltime_seconds,
                "slurm_partition_walltimes",
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
            "/facts/scheduler/htcondor/collector_host",
            bundle.htcondor.collector_host,
            "htcondor_collector_host",
        )
        add(
            "/facts/scheduler/htcondor/file_transfer_supported",
            bundle.htcondor.file_transfer_supported,
            "htcondor_machine_ads",
        )
        for field in ("machine_count", "cpu_cores", "memory_mib", "advertised_gpus"):
            add(
                f"/facts/scheduler/htcondor/pool_totals/{field}",
                getattr(bundle.htcondor.pool_totals, field),
                "htcondor_machine_ads",
            )
        for index, cpu_group in enumerate(bundle.htcondor.cpu_groups):
            prefix = f"/facts/scheduler/htcondor/cpu_groups/{index}"
            for field in (
                "cpu_cores_per_machine",
                "machine_count",
                "memory_mib_min",
                "memory_mib_max",
            ):
                add(
                    f"{prefix}/{field}",
                    getattr(cpu_group, field),
                    "htcondor_machine_ads",
                )
        for index, gpu_group in enumerate(bundle.htcondor.gpu_groups):
            prefix = f"/facts/scheduler/htcondor/gpu_groups/{index}"
            for field in (
                "gpu_count_per_machine",
                "cpu_cores_per_machine",
                "machine_count",
                "memory_mib_min",
                "memory_mib_max",
            ):
                add(
                    f"{prefix}/{field}",
                    getattr(gpu_group, field),
                    "htcondor_machine_ads",
                )
    return observations


def _build_partitions(
    observations: dict[str, MeasurementObservation], link: Callable[[str, str], None]
) -> list[PartitionProfile]:
    names = _strings(observations, "/facts/scheduler/partitions") or []
    result: list[PartitionProfile] = []
    for name in names:
        prefix = f"/facts/scheduler/partitions/{name}"
        shape = f"/facts/scheduler/node_shapes/{name}"
        walltime_path = f"{prefix}/maximum_walltime_seconds"
        result.append(
            PartitionProfile(
                name=name,
                maximum_walltime_seconds=_integer(observations, walltime_path),
                maximum_nodes_per_job=None,
                shared_nodes=None,
                node_count=_integer(observations, f"{prefix}/node_count"),
                cpus_per_node=_integer(observations, f"{shape}/cpus"),
                memory_mib_per_node=_integer(observations, f"{shape}/memory_mib"),
                temporary_disk_mib_per_node=_integer(
                    observations,
                    f"{shape}/temporary_disk_mib",
                ),
                gpu_count_per_node=_integer(observations, f"{shape}/gpu_count"),
                gpu_models=_strings(observations, f"{shape}/gpu_models") or [],
                features=_strings(observations, f"{shape}/features") or [],
            )
        )
        link(f"/slurm/partitions/{name}/maximum_walltime_seconds", walltime_path)
        link(f"/slurm/partitions/{name}/node_count", f"{prefix}/node_count")
        for field, observation_field in (
            ("cpus_per_node", "cpus"),
            ("memory_mib_per_node", "memory_mib"),
            ("temporary_disk_mib_per_node", "temporary_disk_mib"),
            ("gpu_count_per_node", "gpu_count"),
            ("gpu_models", "gpu_models"),
            ("features", "features"),
        ):
            link(
                f"/slurm/partitions/{name}/{field}",
                f"{shape}/{observation_field}",
            )
    link("/slurm/partitions", "/facts/scheduler/partitions")
    return result


def _build_htcondor_resources(
    observations: dict[str, MeasurementObservation],
    link: Callable[[str, str], None],
) -> tuple[
    HTCondorPoolTotalsProfile,
    list[HTCondorCPUGroupProfile],
    list[HTCondorGPUGroupProfile],
]:
    """Build the pool snapshot and broad HTCondor resource groups."""

    totals_prefix = "/facts/scheduler/htcondor/pool_totals"
    totals = HTCondorPoolTotalsProfile(
        machine_count=_integer(observations, f"{totals_prefix}/machine_count") or 0,
        cpu_cores=_integer(observations, f"{totals_prefix}/cpu_cores") or 0,
        memory_mib=_integer(observations, f"{totals_prefix}/memory_mib") or 0,
        advertised_gpus=_integer(
            observations, f"{totals_prefix}/advertised_gpus"
        )
        or 0,
    )
    for field in ("machine_count", "cpu_cores", "memory_mib", "advertised_gpus"):
        link(
            f"/htcondor/pool_totals/{field}",
            f"{totals_prefix}/{field}",
        )

    cpu_indices = _group_indices(observations, "cpu_groups")
    cpu_groups: list[HTCondorCPUGroupProfile] = []
    for index in cpu_indices:
        prefix = f"/facts/scheduler/htcondor/cpu_groups/{index}"
        cpus = _integer(observations, f"{prefix}/cpu_cores_per_machine")
        count = _integer(observations, f"{prefix}/machine_count")
        if cpus is None or count is None:
            continue
        cpu_groups.append(
            HTCondorCPUGroupProfile(
                cpu_cores_per_machine=cpus,
                machine_count=count,
                memory_mib_min=_integer(observations, f"{prefix}/memory_mib_min"),
                memory_mib_max=_integer(observations, f"{prefix}/memory_mib_max"),
            )
        )
        for field in (
            "cpu_cores_per_machine",
            "machine_count",
            "memory_mib_min",
            "memory_mib_max",
        ):
            link(f"/htcondor/cpu_groups/{index}/{field}", f"{prefix}/{field}")

    gpu_indices = _group_indices(observations, "gpu_groups")
    gpu_groups: list[HTCondorGPUGroupProfile] = []
    for index in gpu_indices:
        prefix = f"/facts/scheduler/htcondor/gpu_groups/{index}"
        gpus = _integer(observations, f"{prefix}/gpu_count_per_machine")
        cpus = _integer(observations, f"{prefix}/cpu_cores_per_machine")
        count = _integer(observations, f"{prefix}/machine_count")
        if gpus is None or cpus is None or count is None:
            continue
        gpu_groups.append(
            HTCondorGPUGroupProfile(
                gpu_count_per_machine=gpus,
                cpu_cores_per_machine=cpus,
                machine_count=count,
                memory_mib_min=_integer(observations, f"{prefix}/memory_mib_min"),
                memory_mib_max=_integer(observations, f"{prefix}/memory_mib_max"),
            )
        )
        for field in (
            "gpu_count_per_machine",
            "cpu_cores_per_machine",
            "machine_count",
            "memory_mib_min",
            "memory_mib_max",
        ):
            link(f"/htcondor/gpu_groups/{index}/{field}", f"{prefix}/{field}")
    return totals, cpu_groups, gpu_groups


def _group_indices(
    observations: dict[str, MeasurementObservation],
    group_name: str,
) -> list[int]:
    """Return sorted group array indices represented in measurement evidence."""

    pattern = re.compile(
        rf"^/facts/scheduler/htcondor/{re.escape(group_name)}/(\d+)/"
    )
    return sorted(
        {
            int(match.group(1))
            for path in observations
            if (match := pattern.match(path)) is not None
        }
    )


def _build_storage(
    observations: dict[str, MeasurementObservation],
    link: Callable[[str, str], None],
) -> list[StorageProfile]:
    """Build common storage roles and generalize only measured path components."""

    observed_names = {
        match.group(1)
        for path in observations
        if (match := _STORAGE_PATH.match(path)) is not None
    }
    names = sorted(observed_names)
    username = _string(observations, "/facts/user/username")
    groups = _strings(observations, "/facts/user/groups") or []
    result: list[StorageProfile] = []
    for name in names:
        prefix = f"/facts/storage/filesystems/{name}"
        location_id = _string(observations, f"{prefix}/id") or name
        role = _string(observations, f"{prefix}/role") or "storage"
        path = f"{prefix}/path"
        measured_path = _string(observations, path)
        pattern_path = f"{prefix}/path_pattern"
        measured_pattern = _string(observations, pattern_path)
        path_pattern = _path_pattern(
            measured_pattern or measured_path,
            username=username,
            groups=groups,
        )
        result.append(
            StorageProfile(
                id=location_id,
                name=role.replace("_", " ").title(),
                role=role,
                environment_variables=(
                    _strings(observations, f"{prefix}/environment_variables") or []
                ),
                path_pattern=path_pattern,
                filesystem_type=_string(observations, f"{prefix}/filesystem_type"),
                login_readable=_boolean(observations, f"{prefix}/readable"),
                login_writable=_boolean(observations, f"{prefix}/writable"),
            )
        )
        for field in ("id", "role", "environment_variables"):
            link(f"/storage/{name}/{field}", f"{prefix}/{field}")
        if path_pattern is not None:
            link(
                f"/storage/{name}/path_pattern",
                pattern_path if _observed(observations, pattern_path) else path,
            )
            if path_pattern and "{username}" in path_pattern:
                link(f"/storage/{name}/path_pattern", "/facts/user/username")
            if path_pattern and "{group}" in path_pattern:
                link(f"/storage/{name}/path_pattern", "/facts/user/groups")
        for field in ("filesystem_type", "readable", "writable"):
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
    groups: list[str],
) -> str | None:
    """Replace exact measured path components with supported profile placeholders."""

    if path is None:
        return None
    replacements = {group: "{group}" for group in groups}
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
            name="universe",
            syntax=["universe = {universe}"],
            required=None,
            example="vanilla",
        ),
        SubmissionOption(
            name="executable",
            syntax=["executable = {path}"],
            required=None,
            example="<executable>",
        ),
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
        SubmissionOption(
            name="should_transfer_files",
            syntax=["should_transfer_files = {mode}"],
            required=None,
            example="yes",
            allowed_values=["yes", "no", "if_needed"],
        ),
        SubmissionOption(
            name="when_to_transfer_output",
            syntax=["when_to_transfer_output = {mode}"],
            required=None,
            example="on_exit",
            allowed_values=["on_exit", "on_exit_or_evict"],
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
    if not storage:
        items.append(
            UnresolvedWorkItem(
                field="/storage",
                reason="No reviewed storage location was observed from the login node.",
                next_action="login_measurement",
                action_id="storage_path_input",
            )
        )
    if submit_command is None:
        items.append(
            UnresolvedWorkItem(
                field=f"/{scheduler}/submit_command",
                reason="The submit executable was not observed as available.",
                next_action="user_input",
                action_id="submit_command_input",
            )
        )
    if scheduler == "slurm":
        for partition in partitions:
            for field in (
                "maximum_walltime_seconds",
                "maximum_nodes_per_job",
                "shared_nodes",
                "gpu_count_per_node",
                "gpu_models",
                "features",
            ):
                items.append(
                    UnresolvedWorkItem(
                        field=f"/slurm/partitions/{partition.name}/{field}",
                        reason=(
                            "Visible scheduler configuration does not establish "
                            "enforced policy."
                        ),
                        next_action="additional_documentation",
                        action_id="partition_policy_search",
                    )
                )
    else:
        for field in (
            "guaranteed_runtime",
            "preemptible",
            "submission_host",
            "machine_requirements_supported",
            "dynamic_slots_enabled",
            "bulk_submission_supported",
            "completion_email_supported",
        ):
            items.append(
                UnresolvedWorkItem(
                    field=f"/htcondor/{field}",
                    reason="Login measurements do not establish this HTCondor site policy.",
                    next_action="additional_documentation",
                    action_id="htcondor_site_policy_search",
                )
            )
    for option in submission_options:
        if option.required is None:
            items.append(
                UnresolvedWorkItem(
                    field=(
                        f"/slurm/options/{option.name}/required"
                        if scheduler == "slurm"
                        else f"/htcondor/submit_attributes/{option.name}/required"
                    ),
                    reason="Login measurements do not establish whether this option is required.",
                    next_action="additional_documentation",
                    action_id="submission_policy_search",
                )
            )
        items.append(
            UnresolvedWorkItem(
                field=(
                    f"/slurm/options/{option.name}/support"
                    if scheduler == "slurm"
                    else f"/htcondor/submit_attributes/{option.name}/support"
                ),
                reason="Login measurements do not establish site support for this option.",
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
        for field in ("tcp_connect", "verified_ports", "suggested_port_range"):
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
                    field=f"/storage/{item.id}/path_pattern",
                    reason="The storage role was not observed from the login node.",
                    next_action="login_measurement",
                    action_id=f"{item.id}_path",
                )
            )
        for field in (
            "compute_visible",
            "compute_readable",
            "compute_writable",
            "shared_across_compute_nodes",
        ):
            items.append(
                UnresolvedWorkItem(
                    field=f"/storage/{item.id}/{field}",
                    reason="Login-node access does not establish compute-node access.",
                    next_action="run_pilot",
                    action_id=f"{item.id}_{field}",
                )
            )
        for field in ("backup_policy", "purge_after_days", "purge_condition"):
            items.append(
                UnresolvedWorkItem(
                    field=f"/storage/{item.id}/{field}",
                    reason="Storage policy requires authoritative documentation.",
                    next_action="additional_documentation",
                    action_id="storage_policy_search",
                )
            )
    for field in (
        "allocation_required",
        "charging_unit",
        "charging_model",
        "filesystem_storage_charged",
    ):
        items.append(
            UnresolvedWorkItem(
                field=f"/accounting/{field}",
                reason="Accounting policy cannot be established by login measurement.",
                next_action="additional_documentation",
                action_id="accounting_policy_search",
            )
        )
    return items


def _section_status(*, resources: bool, storage: bool) -> SectionStatus:
    """Return one compact status map for actionable profile sections."""

    return SectionStatus(
        scheduler="measured",
        submission="partial",
        resources="measured" if resources else "partial",
        network="partial",
        storage="partial",
        accounting="partial",
    )


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
