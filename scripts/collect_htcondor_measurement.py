#!/usr/bin/env python3
"""Collect one generic HTCondor login measurement without submitting a job."""

from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
import re
import shutil
import socket
import stat
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "0.7"
COLLECTOR_VERSION = "0.7.0"
COMMAND_TIMEOUT_SECONDS = 60
CONDOR_COMMANDS = ("condor_status", "condor_submit", "condor_q", "condor_history")
CONDOR_ATTRIBUTES = (
    "Name,Machine,SlotType,PartitionableSlot,Cpus,Memory,TotalCpus,TotalMemory,"
    "TotalSlotCpus,TotalSlotMemory,GPUs,TotalGPUs,TotalSlotGPUs,DetectedGPUs,"
    "HasFileTransfer"
)
STORAGE_ENVIRONMENT = re.compile(
    r"(^|_)(HOME|SCRATCH|PSCRATCH|WORK|WORKDIR|MEMBERWORK|PROJECT|PROJECTS|"
    r"DATA|STORAGE|SHARED|ARCHIVE|NOBACKUP)(_|$)",
    re.IGNORECASE,
)
STORAGE_ROOTS = (
    "/groups",
    "/group",
    "/projects",
    "/project",
    "/work",
    "/scratch",
    "/data",
    "/storage",
)


def run_command(command: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> str | None:
    """Run one fixed argument array and return stdout, or null on failure."""

    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def slugify(value: str) -> str:
    """Return a stable lowercase site or field identifier."""

    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "unknown"


def unique(values: list[str]) -> list[str]:
    """Return non-empty strings once while preserving input order."""

    result: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in result:
            result.append(value)
    return result


def as_int(value: Any) -> int | None:
    """Convert one numeric ClassAd value to an integer."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool | None:
    """Convert common ClassAd boolean representations."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.casefold() == "true":
            return True
        if value.casefold() == "false":
            return False
    return None


def first_int(ad: dict[str, Any], names: tuple[str, ...]) -> int | None:
    """Return the first available integer ClassAd attribute."""

    for name in names:
        value = as_int(ad.get(name))
        if value is not None:
            return value
    return None


def machine_name(ad: dict[str, Any]) -> str | None:
    """Return the machine hostname represented by one slot advertisement."""

    machine = ad.get("Machine")
    if isinstance(machine, str) and machine.strip():
        return machine.strip()
    name = ad.get("Name")
    if isinstance(name, str) and name.strip():
        return name.split("@", 1)[-1].strip()
    return None


def is_partitionable(ad: dict[str, Any]) -> bool:
    """Return whether the ad represents a partitionable parent slot."""

    return (
        as_bool(ad.get("PartitionableSlot")) is True
        or str(ad.get("SlotType", "")).casefold() == "partitionable"
    )


def machine_total(
    ads: list[dict[str, Any]],
    total_name: str,
    slot_total_name: str,
    current_name: str,
) -> int | None:
    """Normalize machine resources across partitionable or static slots."""

    totals = [as_int(ad.get(total_name)) for ad in ads]
    totals = [value for value in totals if value is not None]
    if totals:
        return max(totals)

    parents = [
        first_int(ad, (slot_total_name, current_name))
        for ad in ads
        if is_partitionable(ad)
    ]
    parents = [value for value in parents if value is not None]
    if parents:
        return max(parents)

    slots = [first_int(ad, (slot_total_name, current_name)) for ad in ads]
    slots = [value for value in slots if value is not None]
    return sum(slots) if slots else None


def advertised_gpu_count(ads: list[dict[str, Any]]) -> int:
    """Return the advertised GPU total, treating missing GPU resources as zero."""

    totals = [as_int(ad.get("TotalGPUs")) for ad in ads]
    totals = [value for value in totals if value is not None]
    if totals:
        return max(totals)

    parents = [
        first_int(ad, ("TotalSlotGPUs", "GPUs"))
        for ad in ads
        if is_partitionable(ad)
    ]
    parents = [value for value in parents if value is not None]
    if parents:
        return max(parents)

    slots = [first_int(ad, ("TotalSlotGPUs", "GPUs")) for ad in ads]
    slots = [value for value in slots if value is not None]
    if slots:
        return sum(slots)

    identifiers: set[str] = set()
    for ad in ads:
        raw = ad.get("DetectedGPUs")
        if isinstance(raw, str):
            identifiers.update(re.findall(r"[A-Za-z0-9_.:-]+", raw))
        elif isinstance(raw, list):
            identifiers.update(str(value) for value in raw)
    return len(identifiers)


def condor_version() -> str | None:
    """Return the local HTCondor client version."""

    output = run_command(["condor_version"])
    if not output:
        return None
    match = re.search(r"\$CondorVersion:\s*([^$]+)\$", output)
    return match.group(1).strip() if match else output.splitlines()[0]


def query_condor_ads(pool: str | None) -> list[dict[str, Any]]:
    """Run the single reviewed machine ClassAd query."""

    command = ["condor_status"]
    if pool:
        command.extend(["-pool", pool])
    command.extend(
        [
            "-constraint",
            'SlotType =!= "Dynamic"',
            "-json",
            "-attributes",
            CONDOR_ATTRIBUTES,
        ]
    )
    output = run_command(command)
    if not output:
        return []
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def summarize_condor(pool: str | None) -> dict[str, Any]:
    """Group visible machines into broad CPU and GPU resource shapes."""

    ads = query_condor_ads(pool)
    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ad in ads:
        name = machine_name(ad)
        if name:
            by_machine[name].append(ad)

    machines: list[dict[str, Any]] = []
    for name, machine_ads in sorted(by_machine.items()):
        machines.append(
            {
                "name": name,
                "cpus": machine_total(
                    machine_ads, "TotalCpus", "TotalSlotCpus", "Cpus"
                ),
                "memory_mib": machine_total(
                    machine_ads, "TotalMemory", "TotalSlotMemory", "Memory"
                ),
                "gpus": advertised_gpu_count(machine_ads),
                "file_transfer": next(
                    (
                        value
                        for ad in machine_ads
                        if (value := as_bool(ad.get("HasFileTransfer"))) is not None
                    ),
                    None,
                ),
            }
        )

    classified = [
        machine
        for machine in machines
        if isinstance(machine["cpus"], int) and isinstance(machine["memory_mib"], int)
    ]
    unclassified = [machine["name"] for machine in machines if machine not in classified]

    cpu_members: dict[int, list[dict[str, Any]]] = defaultdict(list)
    gpu_members: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for machine in classified:
        cpu_members[machine["cpus"]].append(machine)
        if machine["gpus"] > 0:
            gpu_members[(machine["gpus"], machine["cpus"])].append(machine)

    def memory_range(members: list[dict[str, Any]]) -> tuple[int, int]:
        values = [machine["memory_mib"] for machine in members]
        return min(values), max(values)

    cpu_groups = []
    for cpus, members in cpu_members.items():
        minimum, maximum = memory_range(members)
        cpu_groups.append(
            {
                "cpu_cores_per_machine": cpus,
                "machine_count": len(members),
                "memory_mib_min": minimum,
                "memory_mib_max": maximum,
                "example_machines": sorted(machine["name"] for machine in members)[:3],
            }
        )
    cpu_groups.sort(key=lambda item: (-item["machine_count"], item["cpu_cores_per_machine"]))

    gpu_groups = []
    for (gpus, cpus), members in gpu_members.items():
        minimum, maximum = memory_range(members)
        gpu_groups.append(
            {
                "gpu_count_per_machine": gpus,
                "cpu_cores_per_machine": cpus,
                "machine_count": len(members),
                "memory_mib_min": minimum,
                "memory_mib_max": maximum,
                "example_machines": sorted(machine["name"] for machine in members)[:3],
            }
        )
    gpu_groups.sort(
        key=lambda item: (
            -item["machine_count"],
            item["gpu_count_per_machine"],
            item["cpu_cores_per_machine"],
        )
    )

    transfer_values = [machine["file_transfer"] for machine in machines]
    file_transfer = (
        transfer_values[0]
        if transfer_values
        and transfer_values[0] is not None
        and all(value == transfer_values[0] for value in transfer_values)
        else None
    )
    commands = [command for command in CONDOR_COMMANDS if shutil.which(command)]
    return {
        "version": condor_version(),
        "available_commands": commands,
        "submit_command_available": "condor_submit" in commands,
        "collector_host": run_command(["condor_config_val", "COLLECTOR_HOST"]),
        "file_transfer_supported": file_transfer,
        "pool_totals": {
            "machine_count": len(machines),
            "cpu_cores": sum(machine["cpus"] or 0 for machine in machines),
            "memory_mib": sum(machine["memory_mib"] or 0 for machine in machines),
            "advertised_gpus": sum(machine["gpus"] for machine in machines),
        },
        "cpu_groups": cpu_groups,
        "gpu_groups": gpu_groups,
        "unclassified_machine_count": len(unclassified),
        "unclassified_example_machines": sorted(unclassified)[:3],
    }


def storage_role(value: str) -> str:
    """Derive a broad role from an environment variable or root name."""

    value = value.casefold()
    if "home" in value:
        return "home"
    if "scratch" in value or "nobackup" in value:
        return "scratch"
    if "project" in value or "group" in value:
        return "project"
    if "work" in value:
        return "work"
    if "data" in value:
        return "data"
    if "archive" in value:
        return "archive"
    if "shared" in value:
        return "shared"
    return "storage"


def path_pattern(path: str, username: str, groups: list[str]) -> str:
    """Replace exact user and Unix-group path components with placeholders."""

    aliases = {alias for group in groups for alias in group_aliases(group)}
    replacements = {
        username: "{username}",
        **{group: "{group}" for group in aliases},
    }
    return "/".join(replacements.get(part, part) for part in path.split("/"))


def group_aliases(group: str) -> set[str]:
    """Return the exact Unix group and a conservative numeric-suffix alias."""

    stripped = re.sub(r"[-_]\d+$", "", group)
    return {group, stripped} if stripped else {group}


def collect_storage(
    *,
    extra_paths: list[str],
    extra_roots: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Check only reviewed environment paths and exact user/group candidates."""

    username = getpass.getuser()
    try:
        groups = sorted({grp.getgrgid(gid).gr_name for gid in os.getgroups()})
    except (KeyError, OSError):
        groups = []

    candidates: dict[str, dict[str, Any]] = {}

    def add(path: str, role: str, environment_variable: str | None = None) -> None:
        path = os.path.expandvars(os.path.expanduser(path))
        if not os.path.isabs(path):
            return
        resolved = os.path.realpath(path)
        if not os.path.isdir(resolved) or not os.access(resolved, os.R_OK | os.X_OK):
            return
        item = candidates.setdefault(
            resolved,
            {"role": role, "environment_variables": []},
        )
        if environment_variable and environment_variable not in item["environment_variables"]:
            item["environment_variables"].append(environment_variable)

    add(str(Path.home()), "home", "HOME")
    for name, value in os.environ.items():
        if value and STORAGE_ENVIRONMENT.search(name):
            add(value, storage_role(name), name)

    roots = unique([*STORAGE_ROOTS, *extra_roots])
    for root in roots:
        role = storage_role(Path(root).name)
        add(str(Path(root) / username), role)
        for group in groups:
            for alias in group_aliases(group):
                add(str(Path(root) / alias), role)

    for value in extra_paths:
        role, separator, raw_path = value.partition("=")
        add(raw_path if separator else value, role if separator else "storage")

    locations = []
    role_counts: dict[str, int] = defaultdict(int)
    for observed_path, candidate in sorted(candidates.items()):
        role = candidate["role"]
        role_counts[role] += 1
        location_id = role if role_counts[role] == 1 else f"{role}-{role_counts[role]}"
        try:
            permissions = stat.filemode(Path(observed_path).stat().st_mode)
        except OSError:
            permissions = None
        locations.append(
            {
                "id": location_id,
                "role": role,
                "environment_variables": sorted(candidate["environment_variables"]),
                "observed_path": observed_path,
                "path_pattern": path_pattern(observed_path, username, groups),
                "exists": True,
                "readable": os.access(observed_path, os.R_OK),
                "writable": os.access(observed_path, os.W_OK),
                "executable": os.access(observed_path, os.X_OK),
                "permissions": permissions,
                "filesystem_type": run_command(
                    ["stat", "-f", "-c", "%T", observed_path], timeout=5
                ),
            }
        )
    return {"locations": locations}


def site_facts(
    site_name: str,
    keywords: list[str],
    documentation_domains: list[str],
) -> dict[str, Any]:
    """Collect identity and platform facts used to scope documentation discovery."""

    fqdn = (run_command(["hostname", "-f"], timeout=5) or socket.getfqdn()).lower()
    hostname, separator, dns_suffix = fqdn.partition(".")
    domain = ".".join(fqdn.split(".")[-2:]) if fqdn.count(".") >= 1 else None
    os_release: dict[str, str] = {}
    for line in (run_command(["cat", "/etc/os-release"], timeout=5) or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os_release[key] = value.strip().strip("\"'")
    try:
        groups = sorted({grp.getgrgid(gid).gr_name for gid in os.getgroups()})
    except (KeyError, OSError):
        groups = []
    memory_text = run_command(["cat", "/proc/meminfo"], timeout=5) or ""
    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", memory_text, re.MULTILINE)
    return {
        "site_id": slugify(site_name),
        "site_name": site_name,
        "aliases": [],
        "hostname": hostname or None,
        "fqdn": fqdn or None,
        "dns_suffix": dns_suffix if separator else None,
        "hostname_patterns": [f"*.{dns_suffix}"] if separator else [],
        "documentation_domains": unique(
            [*documentation_domains, *([domain] if domain else [])]
        ),
        "preferred_path_tokens": unique([slugify(site_name), "condor", *keywords]),
        "username": getpass.getuser(),
        "uid": os.getuid(),
        "groups": groups,
        "home_directory": os.environ.get("HOME"),
        "working_directory": os.getcwd(),
        "os_id": os_release.get("ID"),
        "os_version": os_release.get("VERSION_ID"),
        "kernel_release": run_command(["uname", "-r"], timeout=5),
        "architecture": run_command(["uname", "-m"], timeout=5),
        "cpu_count": os.cpu_count(),
        "available_memory_bytes": int(match.group(1)) * 1024 if match else None,
    }


def parse_args() -> argparse.Namespace:
    """Parse the small set of optional site hints."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--short-site-name", required=True)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--documentation-domain", action="append", default=[])
    parser.add_argument("--pool")
    parser.add_argument(
        "--storage-path",
        action="append",
        default=[],
        metavar="ROLE=/ABSOLUTE/PATH",
    )
    parser.add_argument("--storage-root", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("login-measurements.json"))
    return parser.parse_args()


def main() -> int:
    """Collect one measurement artifact and report concise progress."""

    args = parse_args()
    print("[1/4] Collecting login-node identity and storage")
    common = site_facts(
        args.short_site_name,
        args.keyword,
        args.documentation_domain,
    )
    storage = collect_storage(
        extra_paths=args.storage_path,
        extra_roots=args.storage_root,
    )
    print("[2/4] Querying visible HTCondor machine ClassAds")
    condor = summarize_condor(args.pool)
    detected = bool(condor["available_commands"])
    print(
        "[3/4] Grouped "
        f"{condor['pool_totals']['machine_count']} machine(s) into "
        f"{len(condor['cpu_groups'])} CPU and {len(condor['gpu_groups'])} GPU group(s)"
    )
    measurement = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "evidence_source": "measured",
        "collector_version": COLLECTOR_VERSION,
        "detected_schedulers": ["htcondor"] if detected else [],
        "site_facts": common,
        "storage": storage,
        "slurm": None,
        "htcondor": condor if detected else None,
        "networking": None,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(measurement, indent=2) + "\n", encoding="utf-8")
    print(f"[4/4] Wrote {args.output}; no batch job was submitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
