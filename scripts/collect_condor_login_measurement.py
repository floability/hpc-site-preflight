#!/usr/bin/env python3
"""
Collect a measured HTCondor login-node snapshot.

Machines are grouped exactly by:
    (CPU cores per machine, memory MiB per machine, GPU count per machine)

This output is a login measurement, not a final site profile.
"""

from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "0.6"
DEFAULT_COLLECTOR_VERSION = "0.5.0"

MACHINE_ATTRIBUTES = [
    "Name",
    "Machine",
    "Arch",
    "OpSys",
    "OpSysAndVer",
    "SlotType",
    "PartitionableSlot",
    "Cpus",
    "Memory",
    "Disk",
    "TotalCpus",
    "TotalMemory",
    "TotalDisk",
    "TotalSlotCpus",
    "TotalSlotMemory",
    "TotalSlotDisk",
    "GPUs",
    "TotalGPUs",
    "TotalSlotGPUs",
    "DetectedGPUs",
    "FileSystemDomain",
    "UidDomain",
    "HasFileTransfer",
    "CondorVersion",
]

GPU_ID_KEYS = (
    "UUID",
    "DeviceUUID",
    "DeviceUuid",
    "DeviceId",
    "DeviceID",
    "Id",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_command(command: list[str], *, optional: bool = False) -> str | None:
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        if optional:
            return None
        raise RuntimeError(f"Command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        if optional:
            return None
        message = exc.stderr.strip() or exc.stdout.strip() or "unknown error"
        raise RuntimeError(
            f"Command failed: {' '.join(command)}\n{message}"
        ) from exc

    return result.stdout


def run_json_command(
    command: list[str],
    *,
    optional: bool = False,
) -> list[dict[str, Any]]:
    output = run_command(command, optional=optional)
    if output is None:
        return []

    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        if optional:
            return []
        raise RuntimeError(
            f"Command did not return valid JSON: {' '.join(command)}"
        ) from exc

    if not isinstance(value, list):
        if optional:
            return []
        raise RuntimeError(f"Expected a JSON list from: {' '.join(command)}")

    return [item for item in value if isinstance(item, dict)]


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def first_defined(record: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        value = record.get(name)
        if value is not None:
            return value
    return None


def common_value(values: Iterable[Any], expected_count: int) -> Any:
    """Return a scalar only when every item has the same non-null value."""
    values = list(values)
    if len(values) != expected_count or any(value is None for value in values):
        return None
    first = values[0]
    return first if all(value == first for value in values) else None


def numeric_value_and_range(
    values: Iterable[int | None],
    expected_count: int,
) -> tuple[int | None, dict[str, int] | None]:
    """
    Return a scalar if every machine has the same value.
    Otherwise return a min/max range over the observed numeric values.
    """
    values = list(values)
    common = common_value(values, expected_count)
    if isinstance(common, int):
        return common, None

    observed = [value for value in values if isinstance(value, int)]
    if not observed:
        return None, None

    return None, {
        "minimum": min(observed),
        "maximum": max(observed),
    }


def normalize_machine_name(ad: dict[str, Any]) -> str | None:
    machine = ad.get("Machine")
    if isinstance(machine, str) and machine.strip():
        return machine.strip()

    name = ad.get("Name")
    if isinstance(name, str) and name.strip():
        name = name.strip()
        return name.split("@", 1)[1] if "@" in name else name

    return None


def is_partitionable(ad: dict[str, Any]) -> bool:
    return (
        as_bool(ad.get("PartitionableSlot")) is True
        or str(ad.get("SlotType", "")).strip().lower() == "partitionable"
    )


def split_identifier_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    if isinstance(value, str):
        text = value.strip().strip("{}[]")
        if not text:
            return []
        return [
            item.strip().strip('"').strip("'")
            for item in re.split(r"[,;\s]+", text)
            if item.strip()
        ]

    return []


def query_machine_ads(pool: str | None) -> list[dict[str, Any]]:
    command = ["condor_status"]
    if pool:
        command.extend(["-pool", pool])

    command.extend(
        [
            "-constraint",
            'SlotType =!= "Dynamic"',
            "-json",
            "-attributes",
            ",".join(MACHINE_ATTRIBUTES),
        ]
    )
    return run_json_command(command)


def extract_gpu_models(ad: dict[str, Any]) -> set[str]:
    """Find configured GPU model attributes such as CUDA0DeviceName."""
    models: set[str] = set()

    for key, value in ad.items():
        normalized = key.replace("_", "").lower()
        if normalized.endswith("devicename") or normalized.endswith("gpumodel"):
            if isinstance(value, str) and value.strip():
                models.add(value.strip())
            elif isinstance(value, list):
                models.update(
                    str(item).strip()
                    for item in value
                    if str(item).strip()
                )

    return models


def extract_gpu_id(ad: dict[str, Any]) -> str | None:
    for key in GPU_ID_KEYS:
        value = ad.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()

    name = ad.get("Name")
    if name is not None and str(name).strip():
        return str(name).strip()

    return None


def collect_gpu_inventory(pool: str | None) -> dict[str, dict[str, Any]]:
    """
    Query GPU-focused ClassAds.

    The full ads are requested because GPU property names may have configured
    prefixes, for example CUDA0DeviceName.
    """
    command = ["condor_status"]
    if pool:
        command.extend(["-pool", pool])
    command.extend(["-gpus", "-json"])

    gpu_ads = run_json_command(command, optional=True)
    by_machine: dict[str, dict[str, Any]] = {}

    for ad in gpu_ads:
        machine = normalize_machine_name(ad)
        if not machine:
            continue

        entry = by_machine.setdefault(
            machine,
            {
                "models": set(),
                "device_ids": set(),
                "ad_count": 0,
            },
        )
        entry["ad_count"] += 1
        entry["models"].update(extract_gpu_models(ad))

        gpu_id = extract_gpu_id(ad)
        if gpu_id:
            entry["device_ids"].add(gpu_id)

    return by_machine


def machine_gpu_count_from_ads(ads: list[dict[str, Any]]) -> int | None:
    machine_totals = [
        value
        for ad in ads
        if (value := as_int(ad.get("TotalGPUs"))) is not None
    ]
    if machine_totals:
        return max(machine_totals)

    if any(is_partitionable(ad) for ad in ads):
        parent_values = [
            value
            for ad in ads
            if is_partitionable(ad)
            if (
                value := as_int(
                    first_defined(ad, ["TotalSlotGPUs", "GPUs"])
                )
            )
            is not None
        ]
        if parent_values:
            return max(parent_values)

    slot_values = [
        value
        for ad in ads
        if (
            value := as_int(first_defined(ad, ["TotalSlotGPUs", "GPUs"]))
        )
        is not None
    ]
    if slot_values:
        return sum(slot_values)

    detected_ids: set[str] = set()
    for ad in ads:
        detected_ids.update(split_identifier_list(ad.get("DetectedGPUs")))

    return len(detected_ids) if detected_ids else None


def machine_total(
    ads: list[dict[str, Any]],
    machine_field: str,
    slot_total_field: str,
    current_field: str,
    *,
    sum_static_fallback: bool,
) -> int | None:
    machine_values = [
        value
        for ad in ads
        if (value := as_int(ad.get(machine_field))) is not None
    ]
    if machine_values:
        return max(machine_values)

    parent_values = [
        value
        for ad in ads
        if is_partitionable(ad)
        if (
            value := as_int(first_defined(ad, [slot_total_field, current_field]))
        )
        is not None
    ]
    if parent_values:
        return max(parent_values)

    static_values = [
        value
        for ad in ads
        if (
            value := as_int(first_defined(ad, [slot_total_field, current_field]))
        )
        is not None
    ]
    if not static_values:
        return None

    return sum(static_values) if sum_static_fallback else max(static_values)


def aggregate_machine(
    machine_name: str,
    ads: list[dict[str, Any]],
    gpu_inventory: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    count = len(ads)
    has_partitionable = any(is_partitionable(ad) for ad in ads)
    has_static = any(not is_partitionable(ad) for ad in ads)

    if has_partitionable and has_static:
        slot_type = "mixed"
    elif has_partitionable:
        slot_type = "partitionable"
    else:
        slot_type = "static"

    cpus = machine_total(
        ads,
        "TotalCpus",
        "TotalSlotCpus",
        "Cpus",
        sum_static_fallback=True,
    )
    memory_mib = machine_total(
        ads,
        "TotalMemory",
        "TotalSlotMemory",
        "Memory",
        sum_static_fallback=True,
    )
    disk_kib = machine_total(
        ads,
        "TotalDisk",
        "TotalSlotDisk",
        "Disk",
        # Execute disk may be shared by static slots. Avoid multiplying it by
        # the number of advertisements when machine-wide TotalDisk is absent.
        sum_static_fallback=False,
    )

    gpu_count = machine_gpu_count_from_ads(ads)
    gpu_details = gpu_inventory.get(machine_name, {})
    gpu_models = sorted(gpu_details.get("models", set()))

    if gpu_count is None:
        device_ids = gpu_details.get("device_ids", set())
        if device_ids:
            gpu_count = len(device_ids)
        elif gpu_details.get("ad_count"):
            gpu_count = int(gpu_details["ad_count"])
        else:
            gpu_count = 0

    return {
        "machine": machine_name,
        "cpus": cpus,
        "memory_mib": memory_mib,
        "disk_mib": round(disk_kib / 1024) if disk_kib is not None else None,
        "gpus": gpu_count,
        "gpu_models": gpu_models,
        "architecture": common_value([ad.get("Arch") for ad in ads], count),
        "operating_system": common_value(
            [ad.get("OpSysAndVer") or ad.get("OpSys") for ad in ads],
            count,
        ),
        "filesystem_domain": common_value(
            [ad.get("FileSystemDomain") for ad in ads],
            count,
        ),
        "uid_domain": common_value(
            [ad.get("UidDomain") for ad in ads],
            count,
        ),
        "file_transfer_supported": common_value(
            [as_bool(ad.get("HasFileTransfer")) for ad in ads],
            count,
        ),
        "partitionable_slot": has_partitionable,
        "slot_type": slot_type,
        "non_dynamic_slot_count": count,
        "execute_condor_version": common_value(
            [ad.get("CondorVersion") for ad in ads],
            count,
        ),
    }


def group_key(machine: dict[str, Any]) -> tuple[int, int, int] | None:
    cpus = machine.get("cpus")
    memory_mib = machine.get("memory_mib")
    gpus = machine.get("gpus")

    if not all(isinstance(value, int) for value in (cpus, memory_mib, gpus)):
        return None

    return cpus, memory_mib, gpus


def build_requirements_hint(
    cpus: int,
    memory_mib: int,
    gpus: int,
    common_gpu_models: list[str] | None,
) -> dict[str, Any]:
    clauses = [
        f"(TotalCpus == {cpus})",
        f"(TotalMemory == {memory_mib})",
    ]

    if gpus > 0:
        clauses.append(f"(TotalGPUs >= {gpus})")

    require_gpus = None
    if gpus > 0 and common_gpu_models and len(common_gpu_models) == 1:
        model = common_gpu_models[0].replace("\\", "\\\\").replace('"', '\\"')
        require_gpus = f'DeviceName =?= "{model}"'

    return {
        "requirements": " && ".join(clauses),
        "request_gpus": gpus if gpus > 0 else None,
        "require_gpus": require_gpus,
    }


def summarize_resource_group(
    key: tuple[int, int, int],
    machines: list[dict[str, Any]],
    example_limit: int,
) -> dict[str, Any]:
    cpus, memory_mib, gpus = key
    count = len(machines)

    disk_value, disk_range = numeric_value_and_range(
        [machine["disk_mib"] for machine in machines],
        count,
    )
    slot_count_value, slot_count_range = numeric_value_and_range(
        [machine["non_dynamic_slot_count"] for machine in machines],
        count,
    )

    gpu_model_lists = [machine["gpu_models"] for machine in machines]
    common_gpu_models = common_value(gpu_model_lists, count)
    all_gpu_models = sorted(
        {
            model
            for machine in machines
            for model in machine["gpu_models"]
        }
    )

    return {
        "name": f"cpu-{cpus}_memory-{memory_mib}-mib_gpu-{gpus}",
        "machine_count": count,
        "cpus_per_machine": cpus,
        "memory_mib_per_machine": memory_mib,
        "gpu_count_per_machine": gpus,
        "gpu_models": all_gpu_models,
        "disk_mib_per_machine": disk_value,
        "disk_mib_per_machine_range": disk_range,
        "architecture": common_value(
            [machine["architecture"] for machine in machines],
            count,
        ),
        "operating_system": common_value(
            [machine["operating_system"] for machine in machines],
            count,
        ),
        "filesystem_domain": common_value(
            [machine["filesystem_domain"] for machine in machines],
            count,
        ),
        "uid_domain": common_value(
            [machine["uid_domain"] for machine in machines],
            count,
        ),
        "partitionable_slot": common_value(
            [machine["partitionable_slot"] for machine in machines],
            count,
        ),
        "slot_type": common_value(
            [machine["slot_type"] for machine in machines],
            count,
        ),
        "non_dynamic_slot_count_per_machine": slot_count_value,
        "non_dynamic_slot_count_per_machine_range": slot_count_range,
        "execute_condor_version": common_value(
            [machine["execute_condor_version"] for machine in machines],
            count,
        ),
        "example_machines": sorted(
            machine["machine"] for machine in machines
        )[:example_limit],
        "requirements_hint": build_requirements_hint(
            cpus,
            memory_mib,
            gpus,
            common_gpu_models,
        ),
    }


def parse_condor_version() -> str | None:
    output = run_command(["condor_version"], optional=True)
    if not output:
        return None

    match = re.search(r"\$CondorVersion:\s*([^$]+)\$", output)
    if match:
        return match.group(1).strip()

    lines = output.strip().splitlines()
    return lines[0] if lines else None


def available_commands() -> list[str]:
    candidates = [
        "condor_status",
        "condor_submit",
        "condor_q",
        "condor_history",
    ]
    return [name for name in candidates if shutil.which(name)]


def collect_htcondor(
    pool: str | None,
    example_limit: int,
    raw_output: Path | None,
) -> dict[str, Any]:
    machine_ads = query_machine_ads(pool)
    if raw_output:
        raw_output.write_text(
            json.dumps(machine_ads, indent=2) + "\n",
            encoding="utf-8",
        )

    gpu_inventory = collect_gpu_inventory(pool)

    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ad in machine_ads:
        machine_name = normalize_machine_name(ad)
        if machine_name:
            by_machine[machine_name].append(ad)

    machines = [
        aggregate_machine(name, ads, gpu_inventory)
        for name, ads in sorted(by_machine.items())
    ]

    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    unclassified: list[str] = []

    for machine in machines:
        key = group_key(machine)
        if key is None:
            unclassified.append(machine["machine"])
        else:
            groups[key].append(machine)

    resource_groups = [
        summarize_resource_group(key, members, example_limit)
        for key, members in groups.items()
    ]
    resource_groups.sort(
        key=lambda group: (
            -group["machine_count"],
            -group["cpus_per_machine"],
            -group["memory_mib_per_machine"],
            -group["gpu_count_per_machine"],
        )
    )

    file_transfer = common_value(
        [machine["file_transfer_supported"] for machine in machines],
        len(machines),
    )

    commands = available_commands()

    return {
        "version": parse_condor_version(),
        "available_commands": commands,
        "submit_command_available": "condor_submit" in commands,
        "file_transfer_supported": file_transfer,
        "observed_machine_count": len(machines),
        "classified_machine_count": len(machines) - len(unclassified),
        "unclassified_machine_count": len(unclassified),
        "unclassified_example_machines": sorted(unclassified)[:example_limit],
        "resource_group_count": len(resource_groups),
        "resource_groups": resource_groups,
    }


def read_os_release() -> tuple[str | None, str | None]:
    path = Path("/etc/os-release")
    if not path.exists():
        return None, None

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')

    return values.get("ID"), values.get("VERSION_ID")


def available_memory_bytes() -> int | None:
    path = Path("/proc/meminfo")
    if not path.exists():
        return None

    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1]) * 1024

    return None


def collect_site_facts(
    site_id: str,
    site_name: str,
    documentation_domains: list[str],
) -> dict[str, Any]:
    hostname = socket.gethostname()
    fqdn = socket.getfqdn()
    dns_suffix = fqdn.split(".", 1)[1] if "." in fqdn else None
    os_id, os_version = read_os_release()

    try:
        group_ids = os.getgrouplist(getpass.getuser(), os.getgid())
        groups = [grp.getgrgid(group_id).gr_name for group_id in group_ids]
    except (KeyError, OSError):
        groups = []

    return {
        "site_id": site_id,
        "site_name": site_name,
        "aliases": [],
        "hostname": hostname,
        "fqdn": fqdn,
        "dns_suffix": dns_suffix,
        "hostname_patterns": [f"*.{dns_suffix}"] if dns_suffix else [],
        "documentation_domains": documentation_domains,
        "preferred_path_tokens": ["condor"],
        "username": getpass.getuser(),
        "uid": os.getuid(),
        "groups": sorted(set(groups)),
        "home_directory": str(Path.home()),
        "working_directory": str(Path.cwd()),
        "os_id": os_id,
        "os_version": os_version,
        "kernel_release": platform.release(),
        "architecture": platform.machine(),
        "cpu_count": os.cpu_count(),
        "available_memory_bytes": available_memory_bytes(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect an HTCondor login measurement."
    )
    parser.add_argument("--site-id", default="notre-dame-crc")
    parser.add_argument("--site-name", default="Notre Dame CRC")
    parser.add_argument(
        "--documentation-domain",
        action="append",
        default=[],
        help="Repeat to add multiple documentation domains.",
    )
    parser.add_argument(
        "--pool",
        help="Optional collector hostname passed to condor_status -pool.",
    )
    parser.add_argument(
        "--collector-version",
        default=DEFAULT_COLLECTOR_VERSION,
    )
    parser.add_argument("--example-machines", type=int, default=3)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("nd-condor-login-measurement.json"),
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        help="Optionally preserve raw non-dynamic machine ClassAds.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.example_machines < 0:
        print("--example-machines must be non-negative", file=sys.stderr)
        return 2

    documentation_domains = (
        args.documentation_domain if args.documentation_domain else ["nd.edu"]
    )

    try:
        htcondor = collect_htcondor(
            pool=args.pool,
            example_limit=args.example_machines,
            raw_output=args.raw_output,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    measurement = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": utc_now(),
        "evidence_source": "measured",
        "collector_version": args.collector_version,
        "detected_schedulers": ["htcondor"],
        "site_facts": collect_site_facts(
            args.site_id,
            args.site_name,
            documentation_domains,
        ),
        "storage": {},
        "slurm": None,
        "htcondor": htcondor,
    }

    args.output.write_text(
        json.dumps(measurement, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote {args.output}")
    print(f"Observed machines: {htcondor['observed_machine_count']}")
    print(f"Resource groups: {htcondor['resource_group_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
