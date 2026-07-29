#!/usr/bin/env python3
"""Collect one simple, self-contained login-measurements file.

This standard-library-only prototype never submits a job.
"""

from __future__ import annotations

import argparse
import getpass
import grp
import json
import os
import re
import shutil
import socket
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hpc_site_preflight.measurements.htcondor import (
    collect_htcondor as collect_htcondor_facts,
)
from hpc_site_preflight.measurements.storage import collect_storage

COLLECTOR_VERSION = "0.7.1"
COMMAND_TIMEOUT_SECONDS = 60
UNLIMITED_WALLTIME_SECONDS = -1

SLURM_COMMANDS = ("sinfo", "sbatch", "squeue", "sacct")
HTCONDOR_COMMANDS = ("condor_status", "condor_submit", "condor_q", "condor_history")

CommandRunner = Callable[[Sequence[str]], str | None]
ExecutableLookup = Callable[[str], str | None]


def run_fixed_command(arguments: Sequence[str]) -> str | None:
    """Return stdout from one fixed argument array, or null on failure."""

    try:
        completed = subprocess.run(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def slugify(value: str) -> str:
    """Convert a site name into a stable lowercase identifier."""

    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown-site"


def unique(values: Sequence[str]) -> list[str]:
    """Return non-empty values once while preserving order."""

    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def documentation_domain(fqdn: str | None) -> str | None:
    """Use the final two FQDN labels as the initial documentation domain."""

    labels = fqdn.lower().rstrip(".").split(".") if fqdn else []
    return ".".join(labels[-2:]) if len(labels) >= 2 else None


def hostname_pattern(fqdn: str | None) -> str | None:
    """Replace the measured hostname's first label with a wildcard."""

    if not fqdn:
        return None
    _, separator, suffix = fqdn.lower().rstrip(".").partition(".")
    return f"*.{suffix}" if separator else fqdn


def parse_number(value: str) -> int | None:
    """Parse the leading integer from a scheduler field."""

    match = re.match(r"\d+", value)
    return int(match.group()) if match else None


def maximum(left: int | None, right: int | None) -> int | None:
    """Return the larger observed integer while preserving null."""

    values = [value for value in (left, right) if value is not None]
    return max(values) if values else None


def parse_gres(value: str) -> tuple[list[str], int | None, list[str]]:
    """Extract GRES entries, GPU count, and GPU model names."""

    if value.lower() in {"", "(null)", "n/a", "none"}:
        return [], None, []
    entries = [item.strip() for item in value.split(",") if item.strip()]
    gpu_count = 0
    found_gpu = False
    models: list[str] = []
    for entry in entries:
        parts = entry.split("(", 1)[0].split(":")
        if not parts or parts[0].lower() != "gpu":
            continue
        found_gpu = True
        if len(parts) >= 2 and not parts[1].isdigit() and parts[1] not in models:
            models.append(parts[1])
        quantities = [int(part) for part in parts[1:] if part.isdigit()]
        gpu_count += quantities[-1] if quantities else 1
    return entries, (gpu_count if found_gpu else None), models


def parse_slurm_time_limit(value: str) -> int | None:
    """Normalize one Slurm time limit to seconds, using -1 for unlimited."""

    normalized = value.strip().lower()
    if normalized in {"infinite", "unlimited"}:
        return UNLIMITED_WALLTIME_SECONDS
    if normalized in {"", "n/a", "none", "(null)"}:
        return None

    day_parts = normalized.split("-", maxsplit=1)
    days = int(day_parts[0]) if len(day_parts) == 2 and day_parts[0].isdigit() else 0
    clock = day_parts[-1].split(":")
    if not all(part.isdigit() for part in clock):
        return None
    if len(clock) == 3:
        hours, minutes, seconds = (int(part) for part in clock)
    elif len(clock) == 2:
        hours = 0
        minutes, seconds = (int(part) for part in clock)
    elif len(clock) == 1:
        hours, seconds = 0, 0
        minutes = int(clock[0])
    else:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_sinfo_walltimes(text: str | None) -> dict[str, int]:
    """Parse `sinfo -h -o "%P %l"` into normalized per-partition limits."""

    limits: dict[str, int] = {}
    for line in (text or "").splitlines():
        columns = line.split()
        if len(columns) != 2:
            continue
        name = columns[0].rstrip("*")
        seconds = parse_slurm_time_limit(columns[1])
        if name and seconds is not None:
            limits[name] = seconds
    return limits


def parse_sinfo(text: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    """Parse `sinfo -h -o "%P %D %m %c %G"` into a partition array."""

    partitions: dict[str, dict[str, Any]] = {}
    default_partition: str | None = None
    for line in (text or "").splitlines():
        columns = line.split(maxsplit=4)
        if len(columns) != 5:
            continue
        raw_name, nodes_text, memory_text, cpus_text, gres_text = columns
        name = raw_name.rstrip("*")
        if not name:
            continue
        if raw_name.endswith("*"):
            default_partition = name

        gres, gpu_count, gpu_models = parse_gres(gres_text)
        item = partitions.setdefault(
            name,
            {
                "node_count": 0,
                "memory_mib_per_node": None,
                "cpus_per_node": None,
                "gres": [],
                "gpu_count_per_node": None,
                "gpu_models": [],
            },
        )
        item["node_count"] += parse_number(nodes_text) or 0
        item["memory_mib_per_node"] = maximum(
            item["memory_mib_per_node"],
            parse_number(memory_text),
        )
        item["cpus_per_node"] = maximum(
            item["cpus_per_node"],
            parse_number(cpus_text),
        )
        item["gres"] = unique([*item["gres"], *gres])
        item["gpu_count_per_node"] = maximum(item["gpu_count_per_node"], gpu_count)
        item["gpu_models"] = unique([*item["gpu_models"], *gpu_models])
    return default_partition, [
        {"name": name, **values} for name, values in partitions.items()
    ]


def parse_os_release(text: str | None) -> dict[str, str]:
    """Parse standard /etc/os-release values."""

    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip("\"'")
    return values


def parse_available_memory(text: str | None) -> int | None:
    """Return MemAvailable from /proc/meminfo in bytes."""

    match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", text or "", re.MULTILINE)
    return int(match.group(1)) * 1024 if match else None


def detect_schedulers(
    executable_lookup: ExecutableLookup,
) -> tuple[list[str], list[str], list[str]]:
    """Return detected scheduler names and their visible reviewed commands."""

    slurm_commands = [
        command for command in SLURM_COMMANDS if executable_lookup(command)
    ]
    htcondor_commands = [
        command for command in HTCONDOR_COMMANDS if executable_lookup(command)
    ]
    detected: list[str] = []
    if "sinfo" in slurm_commands or "sbatch" in slurm_commands:
        detected.append("slurm")
    if "condor_status" in htcondor_commands or "condor_submit" in htcondor_commands:
        detected.append("htcondor")
    return detected, slurm_commands, htcondor_commands


def collect_slurm(runner: CommandRunner, commands: Sequence[str]) -> dict[str, Any]:
    """Return directly addressable Slurm facts."""

    version_output = runner(["sinfo", "--version"]) if "sinfo" in commands else None
    version = version_output.removeprefix("slurm ").strip() if version_output else None
    partition_output = (
        runner(["sinfo", "-h", "-o", "%P %D %m %c %G"])
        if "sinfo" in commands
        else None
    )
    walltime_output = (
        runner(["sinfo", "-h", "-o", "%P %l"])
        if "sinfo" in commands
        else None
    )
    default_partition, partitions = parse_sinfo(partition_output)
    walltimes = parse_sinfo_walltimes(walltime_output)
    for partition in partitions:
        partition["maximum_walltime_seconds"] = walltimes.get(partition["name"])
    return {
        "version": version,
        "available_commands": list(commands),
        "submit_command_available": "sbatch" in commands,
        "default_partition": default_partition,
        "partitions": partitions,
    }


def collect_measurements(
    *,
    site_name: str,
    keywords: Sequence[str],
    domain_overrides: Sequence[str],
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner = run_fixed_command,
    executable_lookup: ExecutableLookup = shutil.which,
    condor_pool: str | None = None,
    storage_paths: Sequence[str] = (),
    storage_roots: Sequence[str] = (),
) -> dict[str, Any]:
    """Discover identity, storage, and scheduler facts into one JSON object."""

    environment = environment if environment is not None else os.environ
    fqdn_output = runner(["hostname", "-f"])
    fqdn = (fqdn_output or socket.getfqdn() or "").lower().rstrip(".") or None
    hostname = fqdn.split(".", 1)[0] if fqdn else socket.gethostname() or None
    dns_suffix = fqdn.split(".", 1)[1] if fqdn and "." in fqdn else None
    username = getpass.getuser()
    try:
        groups = sorted({grp.getgrgid(group_id).gr_name for group_id in os.getgroups()})
    except (KeyError, OSError):
        groups = []

    derived_domain = documentation_domain(fqdn)
    domains = unique(
        [*domain_overrides, *([derived_domain] if derived_domain else [])]
    )
    pattern = hostname_pattern(fqdn)
    os_release = parse_os_release(runner(["cat", "/etc/os-release"]))
    site_facts = {
        "site_id": slugify(site_name),
        "site_name": site_name,
        "aliases": [],
        "hostname": hostname,
        "fqdn": fqdn,
        "dns_suffix": dns_suffix,
        "hostname_patterns": [pattern] if pattern else [],
        "documentation_domains": domains,
        "preferred_path_tokens": unique([slugify(site_name), *keywords]),
        "username": username,
        "uid": os.getuid(),
        "groups": groups,
        "home_directory": environment.get("HOME"),
        "working_directory": os.getcwd(),
        "os_id": os_release.get("ID"),
        "os_version": os_release.get("VERSION_ID"),
        "kernel_release": runner(["uname", "-r"]),
        "architecture": runner(["uname", "-m"]),
        "cpu_count": os.cpu_count(),
        "available_memory_bytes": parse_available_memory(
            runner(["cat", "/proc/meminfo"])
        ),
    }
    storage = collect_storage(
        username=username,
        groups=groups,
        environment=environment,
        runner=runner,
        extra_paths=storage_paths,
        extra_roots=storage_roots,
    )

    detected, slurm_commands, htcondor_commands = detect_schedulers(executable_lookup)
    return {
        "schema_version": "0.7",
        "collected_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "evidence_source": "measured",
        "collector_version": COLLECTOR_VERSION,
        "detected_schedulers": detected,
        "site_facts": site_facts,
        "storage": storage,
        "slurm": (
            collect_slurm(runner, slurm_commands)
            if "slurm" in detected
            else None
        ),
        "htcondor": (
            collect_htcondor_facts(runner, htcondor_commands, pool=condor_pool)
            if "htcondor" in detected
            else None
        ),
    }


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the few inputs a site user is expected to know."""

    parser = argparse.ArgumentParser(
        description="Discover a site and collect login-node measurements."
    )
    parser.add_argument(
        "--short-site-name",
        "--site-name",
        dest="site_name",
        required=True,
        help="Short name people use for the HPC site, for example Anvil.",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Optional documentation-search word; repeat as needed.",
    )
    parser.add_argument(
        "--documentation-domain",
        action="append",
        default=[],
        help="Optional documentation domain when it differs from the login domain.",
    )
    parser.add_argument("--condor-pool", help="Optional HTCondor collector hostname.")
    parser.add_argument(
        "--storage-path",
        action="append",
        default=[],
        metavar="ROLE=/ABSOLUTE/PATH",
        help="Exact additional storage path; repeat as needed.",
    )
    parser.add_argument(
        "--storage-root",
        action="append",
        default=[],
        help="Root under which exact username and group paths are checked.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("login-measurements.json"),
        help="Output JSON path.",
    )
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    """Collect one result and print short progress."""

    args = parse_args(arguments)
    print(f"[1/3] Discovering {args.site_name} from the current login node")
    result = collect_measurements(
        site_name=args.site_name,
        keywords=args.keyword,
        domain_overrides=args.documentation_domain,
        condor_pool=args.condor_pool,
        storage_paths=args.storage_path,
        storage_roots=args.storage_root,
    )
    scheduler_text = ", ".join(result["detected_schedulers"]) or "none"
    print(f"[2/3] Scheduler detected: {scheduler_text}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{json.dumps(result, indent=2)}\n", encoding="utf-8")
    print(f"[3/3] Wrote {args.output}; no batch job was submitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
