"""Deterministic scheduler, resource, option, and network checks."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Literal

from hpc_site_preflight.backpack.models import BackpackRequirements
from hpc_site_preflight.preflight.models import PreflightIssue, ResourceSelection
from hpc_site_preflight.preflight.remediation import remediation_for
from hpc_site_preflight.profiles.models import (
    HTCondorCPUGroupProfile,
    HTCondorGPUGroupProfile,
    PartitionProfile,
    SiteProfile,
)

_SHORT_SLURM_OPTIONS = {
    "-A": "account",
    "-N": "nodes",
    "-n": "ntasks",
    "-p": "partition",
    "-t": "time",
}


@dataclass
class CompatibilityDecision:
    """Internal values needed to render a final execution plan."""

    selected_resource: ResourceSelection | None = None
    scheduler_values: dict[str, str] = field(default_factory=dict)
    batch_arguments: list[str] = field(default_factory=list)
    manager_ports: list[int] = field(default_factory=list)
    transfer_port_range: str | None = None
    issues: list[PreflightIssue] = field(default_factory=list)

    @property
    def result(self) -> str:
        if any(issue.severity == "error" for issue in self.issues):
            return "blocked"
        if any(issue.severity == "unresolved" for issue in self.issues):
            return "unknown"
        return "ready"


def check_compatibility(
    requirements: BackpackRequirements,
    profile: SiteProfile,
    supplied_values: dict[str, str],
) -> CompatibilityDecision:
    """Compare normalized requirements with one validated site profile."""

    decision = CompatibilityDecision()
    expected = (
        "htcondor"
        if requirements.requested_scheduler == "condor"
        else requirements.requested_scheduler
    )
    if expected is not None and expected != profile.scheduler_type:
        decision.issues.append(
            _issue(
                "/scheduler_type",
                "error",
                f"The command requests {requirements.requested_scheduler}, "
                f"but the site uses {profile.scheduler_type}.",
                requirements.requested_scheduler,
                profile.scheduler_type,
                "scheduler",
            )
        )
        return decision

    if requirements.resources.disk_mb_per_worker is not None:
        decision.issues.append(
            _issue(
                "/resources/disk_mb_per_worker",
                "unresolved",
                "The workflow requests local disk, but the site profile has no "
                "verified disk capacity.",
                requirements.resources.disk_mb_per_worker,
                None,
                "disk",
            )
        )

    if profile.scheduler_type == "slurm":
        _check_slurm(requirements, profile, supplied_values, decision)
    else:
        _check_htcondor(requirements, profile, supplied_values, decision)
    _check_network(requirements, profile, decision)
    return decision


def _check_slurm(
    requirements: BackpackRequirements,
    profile: SiteProfile,
    supplied_values: dict[str, str],
    decision: CompatibilityDecision,
) -> None:
    assert profile.slurm is not None
    command_arguments, command_values = _slurm_batch_values(requirements.batch_options)
    values = {**command_values, **supplied_values}
    requested_partition = values.get("partition")
    partitions = profile.slurm.partitions
    if requested_partition is not None:
        partitions = [item for item in partitions if item.name == requested_partition]

    candidates = [item for item in partitions if _slurm_fits(item, requirements, values)]
    if not candidates:
        severity: Literal["error", "unresolved"] = (
            "error" if profile.slurm.partitions else "unresolved"
        )
        decision.issues.append(
            _issue(
                "/slurm/partitions",
                severity,
                "No visible Slurm partition satisfies the per-worker CPU, memory, "
                "GPU, and walltime request.",
                requirements.resources.model_dump(mode="json"),
                [item.model_dump(mode="json") for item in partitions],
                "resource",
            )
        )
    else:
        selected = min(candidates, key=lambda item: _slurm_waste(item, requirements))
        values.setdefault("partition", selected.name)
        decision.selected_resource = ResourceSelection(
            scheduler="slurm",
            name=selected.name,
            profile_path=f"/slurm/partitions/{selected.name}",
        )

    option_by_name = {option.name: option for option in profile.slurm.options}
    for name in command_values:
        option = option_by_name.get(name)
        if option is not None and option.support == "unsupported":
            decision.issues.append(
                _issue(
                    f"/slurm/options/{name}",
                    "error",
                    f"The command uses Slurm option '{name}', which this site "
                    "documents as unsupported.",
                    command_values[name],
                    "unsupported",
                    "unsupported_option",
                )
            )

    for option in profile.slurm.options:
        if option.required is not True:
            continue
        if option.name == "partition" and decision.selected_resource is None:
            continue
        if option.support == "unsupported":
            decision.issues.append(
                _issue(
                    f"/slurm/options/{option.name}",
                    "error",
                    f"A required Slurm option is documented as unsupported: {option.name}.",
                    True,
                    option.support,
                    "unsupported_option",
                )
            )
            continue
        if option.name == "nodes":
            values.setdefault("nodes", "1")
            condition = (option.condition or "").lower()
            companion = option_by_name.get("ntasks-per-node")
            if (
                "ntasks-per-node" in condition
                and companion is not None
                and companion.support == "supported"
            ):
                values.setdefault("ntasks-per-node", "1")
        if option.name not in values:
            decision.issues.append(
                _issue(
                    f"/slurm/options/{option.name}",
                    "unresolved",
                    f"The site requires Slurm option '{option.name}', but no value was supplied.",
                    True,
                    None,
                    "required_option",
                )
            )

    decision.scheduler_values = values
    decision.batch_arguments = _append_slurm_arguments(command_arguments, command_values, values)


def _check_htcondor(
    requirements: BackpackRequirements,
    profile: SiteProfile,
    supplied_values: dict[str, str],
    decision: CompatibilityDecision,
) -> None:
    assert profile.htcondor is not None
    resources = requirements.resources
    if resources.gpus_per_worker > 0:
        candidates: list[HTCondorGPUGroupProfile | HTCondorCPUGroupProfile] = [
            item
            for item in profile.htcondor.gpu_groups
            if item.gpu_count_per_machine >= resources.gpus_per_worker
            and item.cpu_cores_per_machine >= resources.cores_per_worker
            and _memory_fits(item.memory_mib_max, resources.memory_mb_per_worker)
        ]
        source_path = "/htcondor/gpu_groups"
    else:
        candidates = [
            item
            for item in profile.htcondor.cpu_groups
            if item.cpu_cores_per_machine >= resources.cores_per_worker
            and _memory_fits(item.memory_mib_max, resources.memory_mb_per_worker)
        ]
        source_path = "/htcondor/cpu_groups"

    if not candidates:
        known_groups = (
            profile.htcondor.gpu_groups
            if resources.gpus_per_worker
            else profile.htcondor.cpu_groups
        )
        decision.issues.append(
            _issue(
                source_path,
                "error" if known_groups else "unresolved",
                "No visible HTCondor machine group satisfies the per-worker resource request.",
                resources.model_dump(mode="json"),
                [item.model_dump(mode="json") for item in known_groups],
                "resource",
            )
        )
    else:
        selected = min(
            candidates,
            key=lambda item: (
                item.cpu_cores_per_machine - resources.cores_per_worker,
                (item.memory_mib_max or 10**18)
                - _mb_to_mib(resources.memory_mb_per_worker or 0),
            ),
        )
        group_kind = "gpu" if resources.gpus_per_worker else "cpu"
        name = f"{group_kind}-{selected.cpu_cores_per_machine}-core"
        decision.selected_resource = ResourceSelection(
            scheduler="htcondor",
            name=name,
            profile_path=source_path,
        )

    for attribute in profile.htcondor.submit_attributes:
        if attribute.required is True and attribute.name not in supplied_values:
            decision.issues.append(
                _issue(
                    f"/htcondor/submit_attributes/{attribute.name}",
                    "unresolved",
                    f"The site requires HTCondor attribute '{attribute.name}', "
                    "but no value was supplied.",
                    True,
                    None,
                    "required_option",
                )
            )
    decision.scheduler_values = dict(supplied_values)


def _check_network(
    requirements: BackpackRequirements,
    profile: SiteProfile,
    decision: CompatibilityDecision,
) -> None:
    if requirements.requires_login_compute_network:
        connection = profile.network.login_compute
        if connection.tcp_connect is False:
            decision.issues.append(
                _issue(
                    "/network/login_compute/tcp_connect",
                    "error",
                    "Compute workers cannot connect to the login-node manager.",
                    True,
                    False,
                    "network",
                )
            )
        elif (
            connection.tcp_connect is None
            or not connection.verified_ports
            or len(connection.verified_ports) < 2
        ):
            decision.issues.append(
                _issue(
                    "/network/login_compute/verified_ports",
                    "unresolved",
                    "Two manager ports have not been verified between login and compute nodes.",
                    2,
                    connection.verified_ports,
                    "network",
                )
            )
        else:
            requested = requirements.manager_ports
            decision.manager_ports = (
                requested
                if set(requested).issubset(connection.verified_ports)
                else connection.verified_ports[:2]
            )

    if requirements.requires_compute_compute_network:
        connection = profile.network.compute_compute
        if connection.tcp_connect is False:
            decision.issues.append(
                _issue(
                    "/network/compute_compute/tcp_connect",
                    "error",
                    "Workers cannot establish the requested compute-to-compute connection.",
                    True,
                    False,
                    "network",
                )
            )
        elif connection.tcp_connect is None or connection.suggested_port_range is None:
            decision.issues.append(
                _issue(
                    "/network/compute_compute/suggested_port_range",
                    "unresolved",
                    "No pilot-derived worker transfer range is available.",
                    requirements.worker_transfer_port_range,
                    connection.suggested_port_range,
                    "network",
                )
            )
        else:
            decision.transfer_port_range = connection.suggested_port_range


def _slurm_fits(
    partition: PartitionProfile,
    requirements: BackpackRequirements,
    values: dict[str, str],
) -> bool:
    resources = requirements.resources
    if partition.cpus_per_node is None or partition.cpus_per_node < resources.cores_per_worker:
        return False
    if not _memory_fits(partition.memory_mib_per_node, resources.memory_mb_per_worker):
        return False
    if resources.gpus_per_worker > 0 and (
        partition.gpu_count_per_node is None
        or partition.gpu_count_per_node < resources.gpus_per_worker
    ):
        return False
    walltime = _walltime_seconds(values.get("time"))
    maximum_walltime = partition.maximum_walltime_seconds
    return not (
        walltime is not None
        and maximum_walltime is not None
        and maximum_walltime != -1
        and walltime > maximum_walltime
    )


def _slurm_waste(
    partition: PartitionProfile, requirements: BackpackRequirements
) -> tuple[int, int, int]:
    resources = requirements.resources
    return (
        (partition.gpu_count_per_node or 0) - resources.gpus_per_worker,
        (partition.cpus_per_node or 10**9) - resources.cores_per_worker,
        (partition.memory_mib_per_node or 10**18)
        - _mb_to_mib(resources.memory_mb_per_worker or 0),
    )


def _memory_fits(available_mib: int | None, requested_mb: int | None) -> bool:
    return requested_mb is None or (
        available_mib is not None and available_mib >= _mb_to_mib(requested_mb)
    )


def _mb_to_mib(value: int) -> int:
    """Round decimal MB upward to binary MiB for profile comparison."""

    return (value * 1_000_000 + 1_048_575) // 1_048_576


def _slurm_batch_values(batch_options: str | None) -> tuple[list[str], dict[str, str]]:
    if not batch_options:
        return [], {}
    try:
        arguments = shlex.split(batch_options)
    except ValueError:
        return [], {}
    values: dict[str, str] = {}
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token.startswith("--"):
            name, separator, inline = token[2:].partition("=")
            if separator:
                values[name] = inline
            elif index + 1 < len(arguments) and not arguments[index + 1].startswith("-"):
                values[name] = arguments[index + 1]
                index += 1
        elif token in _SHORT_SLURM_OPTIONS and index + 1 < len(arguments):
            values[_SHORT_SLURM_OPTIONS[token]] = arguments[index + 1]
            index += 1
        index += 1
    return arguments, values


def _append_slurm_arguments(
    arguments: list[str],
    original_values: dict[str, str],
    final_values: dict[str, str],
) -> list[str]:
    result = list(arguments)
    for name, value in final_values.items():
        if name not in original_values:
            result.append(f"--{name}={value}")
    return result


def _walltime_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        day_text, clock = value.split("-", 1) if "-" in value else ("0", value)
        parts = [int(item) for item in clock.split(":")]
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours, minutes, seconds = 0, *parts
        else:
            return None
        return int(day_text) * 86400 + hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None


def _issue(
    field_path: str,
    severity: Literal["error", "unresolved", "warning"],
    reason: str,
    required_value: object,
    site_value: object,
    remediation: str,
) -> PreflightIssue:
    return PreflightIssue(
        field_path=field_path,
        severity=severity,
        reason=reason,
        required_value=required_value,
        site_value=site_value,
        remediation=remediation_for(remediation),
    )
