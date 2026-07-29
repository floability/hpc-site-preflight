"""Normalize one reviewed HTCondor machine ClassAd query."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from typing import Any

CommandRunner = Callable[[Sequence[str]], str | None]

ATTRIBUTES = (
    "Name,Machine,SlotType,PartitionableSlot,Cpus,Memory,TotalCpus,TotalMemory,"
    "TotalSlotCpus,TotalSlotMemory,GPUs,TotalGPUs,TotalSlotGPUs,DetectedGPUs,"
    "HasFileTransfer"
)


def collect_htcondor(
    runner: CommandRunner,
    commands: Sequence[str],
    *,
    pool: str | None = None,
) -> dict[str, Any]:
    """Return pool totals plus broad CPU and GPU machine groups."""

    ads = _query_ads(runner, pool) if "condor_status" in commands else []
    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ad in ads:
        name = _machine_name(ad)
        if name:
            by_machine[name].append(ad)

    machines = [
        {
            "name": name,
            "cpus": _machine_total(machine_ads, "TotalCpus", "TotalSlotCpus", "Cpus"),
            "memory_mib": _machine_total(
                machine_ads,
                "TotalMemory",
                "TotalSlotMemory",
                "Memory",
            ),
            "gpus": _gpu_count(machine_ads),
            "file_transfer": _first_boolean(machine_ads, "HasFileTransfer"),
        }
        for name, machine_ads in sorted(by_machine.items())
    ]
    classified = [
        item
        for item in machines
        if isinstance(item["cpus"], int) and isinstance(item["memory_mib"], int)
    ]
    unclassified = [item["name"] for item in machines if item not in classified]

    cpu_members: dict[int, list[dict[str, Any]]] = defaultdict(list)
    gpu_members: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in classified:
        cpu_members[item["cpus"]].append(item)
        if item["gpus"] > 0:
            gpu_members[(item["gpus"], item["cpus"])].append(item)

    cpu_groups = [
        _cpu_group(cpus, members) for cpus, members in cpu_members.items()
    ]
    cpu_groups.sort(
        key=lambda item: (-item["machine_count"], item["cpu_cores_per_machine"])
    )
    gpu_groups = [
        _gpu_group(gpus, cpus, members)
        for (gpus, cpus), members in gpu_members.items()
    ]
    gpu_groups.sort(
        key=lambda item: (
            -item["machine_count"],
            item["gpu_count_per_machine"],
            item["cpu_cores_per_machine"],
        )
    )

    transfers = [item["file_transfer"] for item in machines]
    file_transfer = (
        transfers[0]
        if transfers
        and transfers[0] is not None
        and all(value == transfers[0] for value in transfers)
        else None
    )
    version_output = runner(["condor_version"]) if commands else None
    collector_host = runner(["condor_config_val", "COLLECTOR_HOST"]) if commands else None
    return {
        "version": _parse_version(version_output),
        "available_commands": list(commands),
        "submit_command_available": "condor_submit" in commands,
        "collector_host": collector_host,
        "file_transfer_supported": file_transfer,
        "pool_totals": {
            "machine_count": len(machines),
            "cpu_cores": sum(item["cpus"] or 0 for item in machines),
            "memory_mib": sum(item["memory_mib"] or 0 for item in machines),
            "advertised_gpus": sum(item["gpus"] for item in machines),
        },
        "cpu_groups": cpu_groups,
        "gpu_groups": gpu_groups,
        "unclassified_machine_count": len(unclassified),
        "unclassified_example_machines": sorted(unclassified)[:3],
    }


def _query_ads(runner: CommandRunner, pool: str | None) -> list[dict[str, Any]]:
    """Run and parse the fixed non-dynamic machine query."""

    command = ["condor_status"]
    if pool:
        command.extend(["-pool", pool])
    command.extend(
        [
            "-constraint",
            'SlotType =!= "Dynamic"',
            "-json",
            "-attributes",
            ATTRIBUTES,
        ]
    )
    output = runner(command)
    if not output:
        return []
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _machine_name(ad: dict[str, Any]) -> str | None:
    """Return one ad's machine hostname."""

    value = ad.get("Machine") or ad.get("Name")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.split("@", 1)[-1].strip()


def _as_int(value: Any) -> int | None:
    """Convert one numeric ClassAd value."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_int(ad: dict[str, Any], names: tuple[str, ...]) -> int | None:
    """Return the first available numeric ClassAd attribute."""

    for name in names:
        value = _as_int(ad.get(name))
        if value is not None:
            return value
    return None


def _partitionable(ad: dict[str, Any]) -> bool:
    """Return whether an ad is a partitionable parent slot."""

    value = ad.get("PartitionableSlot")
    return value is True or str(ad.get("SlotType", "")).casefold() == "partitionable"


def _machine_total(
    ads: list[dict[str, Any]],
    total_name: str,
    slot_total_name: str,
    current_name: str,
) -> int | None:
    """Normalize one machine-wide resource value."""

    totals = [_as_int(ad.get(total_name)) for ad in ads]
    observed_totals = [value for value in totals if value is not None]
    if observed_totals:
        return max(observed_totals)
    parents = [
        _first_int(ad, (slot_total_name, current_name))
        for ad in ads
        if _partitionable(ad)
    ]
    observed_parents = [value for value in parents if value is not None]
    if observed_parents:
        return max(observed_parents)
    slots = [_first_int(ad, (slot_total_name, current_name)) for ad in ads]
    observed_slots = [value for value in slots if value is not None]
    return sum(observed_slots) if observed_slots else None


def _gpu_count(ads: list[dict[str, Any]]) -> int:
    """Return advertised GPUs, treating an absent resource as zero."""

    total = _machine_total(ads, "TotalGPUs", "TotalSlotGPUs", "GPUs")
    if total is not None:
        return total
    identifiers: set[str] = set()
    for ad in ads:
        raw = ad.get("DetectedGPUs")
        if isinstance(raw, list):
            identifiers.update(str(value) for value in raw)
        elif isinstance(raw, str):
            identifiers.update(re.findall(r"[A-Za-z0-9_.:-]+", raw))
    return len(identifiers)


def _first_boolean(ads: list[dict[str, Any]], name: str) -> bool | None:
    """Return the first advertised boolean value."""

    for ad in ads:
        value = ad.get(name)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.casefold() in {"true", "false"}:
            return value.casefold() == "true"
    return None


def _memory_range(members: list[dict[str, Any]]) -> tuple[int, int]:
    """Return the minimum and maximum memory in one group."""

    values = [item["memory_mib"] for item in members]
    return min(values), max(values)


def _cpu_group(cpus: int, members: list[dict[str, Any]]) -> dict[str, Any]:
    """Build one CPU-core group."""

    minimum, maximum = _memory_range(members)
    return {
        "cpu_cores_per_machine": cpus,
        "machine_count": len(members),
        "memory_mib_min": minimum,
        "memory_mib_max": maximum,
        "example_machines": sorted(item["name"] for item in members)[:3],
    }


def _gpu_group(
    gpus: int,
    cpus: int,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one advertised-GPU group."""

    minimum, maximum = _memory_range(members)
    return {
        "gpu_count_per_machine": gpus,
        "cpu_cores_per_machine": cpus,
        "machine_count": len(members),
        "memory_mib_min": minimum,
        "memory_mib_max": maximum,
        "example_machines": sorted(item["name"] for item in members)[:3],
    }


def _parse_version(output: str | None) -> str | None:
    """Extract the version string from `condor_version` output."""

    if not output:
        return None
    match = re.search(r"\$CondorVersion:\s*([^$]+)\$", output)
    return match.group(1).strip() if match else output.splitlines()[0]
