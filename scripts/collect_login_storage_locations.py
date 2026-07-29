#!/usr/bin/env python3
"""
Collect a small list of writable storage locations visible from a login node.

The collector intentionally does NOT inventory every mounted filesystem.
It looks only for likely places where the current user can store data:

  * the user's home directory;
  * storage-related environment variables;
  * user/group directories under common storage roots;
  * paths mentioned by simple quota commands;
  * /tmp and /dev/shm.

For group/project storage, it probes directories derived from the user's Unix
groups and performs a bounded one-level scan of common roots. A child directory
is retained only when the user owns it, belongs to its group, or can write to it.

Example:
    python collect_login_storage_locations.py \
        --output storage-login-measurement.json

Add a site-specific root:
    python collect_login_storage_locations.py \
        --root /some/site/storage \
        --output storage-login-measurement.json
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENV_ROLE_RE = re.compile(
    r"(^|_)(HOME|SCRATCH|WORK|WORKDIR|PROJECT|PROJECTS|DATA|"
    r"STORAGE|SHARED|ARCHIVE|NOBACKUP|PSCRATCH|TMP|TMPDIR|TEMP|"
    r"LOCAL_SCRATCH|MEMBERWORK)(_|$)",
    re.IGNORECASE,
)

SPECIAL_ENV_ROLES = {
    "HOME": "home",
    "SCRATCH": "scratch",
    "PSCRATCH": "scratch",
    "LOCAL_SCRATCH": "scratch",
    "WORK": "work",
    "WORKDIR": "work",
    "MEMBERWORK": "work",
    "PROJECT": "project",
    "PROJECTS": "project",
    "DATA": "data",
    "STORAGE": "storage",
    "SHARED": "shared",
    "ARCHIVE": "archive",
    "NOBACKUP": "scratch",
    "TMP": "temporary",
    "TMPDIR": "temporary",
    "TEMP": "temporary",
}

DEFAULT_ROOTS = [
    "/groups",
    "/group",
    "/projects",
    "/project",
    "/work",
    "/scratch",
    "/data",
    "/store",
    "/storage",
    "/gpfs",
    "/lustre",
    "/fs",
]

ROOT_ROLE = {
    "groups": "project",
    "group": "project",
    "projects": "project",
    "project": "project",
    "work": "work",
    "scratch": "scratch",
    "data": "data",
    "store": "storage",
    "storage": "storage",
    "gpfs": "storage",
    "lustre": "storage",
    "fs": "storage",
}

ROLE_PRIORITY = {
    "home": 0,
    "project": 1,
    "work": 2,
    "scratch": 3,
    "data": 4,
    "storage": 5,
    "shared": 6,
    "archive": 7,
    "temporary": 8,
    "unknown": 9,
}

QUOTA_COMMANDS = [
    ("myquota", ["myquota"]),
    ("quota", ["quota", "-s"]),
]

DETECT_ONLY_QUOTA_TOOLS = [
    "pan_quota",
    "lfs",
    "mmlsquota",
    "beegfs-ctl",
]

ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])(/[^\s,;:()\[\]{}<>\"']+)")


@dataclass
class Candidate:
    path: str
    role: str
    score: int
    sources: set[str] = field(default_factory=set)
    environment_variables: set[str] = field(default_factory=set)
    group_name: str | None = None
    group_path_name: str | None = None


def run_command(
    command: list[str],
    timeout: int = 5,
) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False, ""

    output = "\n".join(
        part.strip()
        for part in (result.stdout, result.stderr)
        if part and part.strip()
    )

    return result.returncode == 0, output


def existing_directory(path: str) -> str | None:
    path = os.path.expandvars(os.path.expanduser(path.strip()))

    if not os.path.isabs(path):
        return None

    try:
        real_path = os.path.realpath(path)
    except OSError:
        return None

    if not os.path.isdir(real_path):
        return None

    return real_path


def can_store(path: str) -> bool:
    return (
        os.path.isdir(path)
        and os.access(path, os.R_OK)
        and os.access(path, os.W_OK)
        and os.access(path, os.X_OK)
    )


def role_from_env_name(name: str) -> str:
    upper = name.upper()

    if upper in SPECIAL_ENV_ROLES:
        return SPECIAL_ENV_ROLES[upper]

    match = ENV_ROLE_RE.search(upper)
    if not match:
        return "unknown"

    token = match.group(2).upper()
    return SPECIAL_ENV_ROLES.get(token, token.lower())


def merge_candidate(
    candidates: dict[str, Candidate],
    path: str,
    role: str,
    score: int,
    source: str,
    environment_variable: str | None = None,
    group_name: str | None = None,
    group_path_name: str | None = None,
) -> None:
    real_path = existing_directory(path)

    if real_path is None or not can_store(real_path):
        return

    current = candidates.get(real_path)

    if current is None:
        current = Candidate(
            path=real_path,
            role=role,
            score=score,
            group_name=group_name,
            group_path_name=group_path_name,
        )
        candidates[real_path] = current
    else:
        if score > current.score:
            current.score = score
            current.role = role

        if current.group_name is None and group_name:
            current.group_name = group_name

        if current.group_path_name is None and group_path_name:
            current.group_path_name = group_path_name

    current.sources.add(source)

    if environment_variable:
        current.environment_variables.add(environment_variable)


def safe_group_context() -> tuple[list[int], dict[int, str]]:
    group_ids = sorted(
        {
            gid
            for gid in {os.getgid(), *os.getgroups()}
            if isinstance(gid, int) and gid >= 0
        }
    )

    names: dict[int, str] = {}

    for gid in group_ids:
        try:
            name = grp.getgrgid(gid).gr_name
        except (KeyError, OSError):
            continue

        if name:
            names[gid] = name

    return group_ids, names


def group_aliases(group_name: str) -> set[str]:
    """
    Produce conservative directory-name aliases.

    Examples:
        dthain-1 -> dthain-1, dthain
        ccl_2    -> ccl_2, ccl
    """
    aliases = {group_name}

    stripped = re.sub(r"[-_]\d+$", "", group_name)
    if stripped:
        aliases.add(stripped)

    return aliases


def discover_home(
    candidates: dict[str, Candidate],
    username: str,
    home_directory: str,
) -> None:
    merge_candidate(
        candidates,
        home_directory,
        role="home",
        score=100,
        source="password_database",
    )

    env_home = os.environ.get("HOME")
    if env_home:
        merge_candidate(
            candidates,
            env_home,
            role="home",
            score=100,
            source="environment",
            environment_variable="HOME",
        )


def discover_storage_environment(
    candidates: dict[str, Candidate],
) -> None:
    for name, value in os.environ.items():
        role = role_from_env_name(name)

        if role == "unknown":
            continue

        # Storage variables should name one directory, not a search path.
        if not value or ":" in value or "://" in value:
            continue

        merge_candidate(
            candidates,
            value,
            role=role,
            score=85,
            source="environment",
            environment_variable=name,
        )


def root_role(root: str) -> str:
    return ROOT_ROLE.get(Path(root).name.lower(), "storage")


def discover_exact_group_paths(
    candidates: dict[str, Candidate],
    roots: list[str],
    username: str,
    group_names: dict[int, str],
) -> None:
    names: list[tuple[str, str | None]] = [(username, None)]

    for group_name in sorted(set(group_names.values())):
        for alias in sorted(group_aliases(group_name)):
            names.append((alias, group_name))

    for root in roots:
        root_path = existing_directory(root)
        if root_path is None:
            continue

        role = root_role(root_path)

        for path_name, group_name in names:
            merge_candidate(
                candidates,
                os.path.join(root_path, path_name),
                role=role,
                score=95 if group_name else 80,
                source="group_path_probe" if group_name else "user_path_probe",
                group_name=group_name,
                group_path_name=path_name if group_name else None,
            )


def discover_group_owned_children(
    candidates: dict[str, Candidate],
    roots: list[str],
    uid: int,
    group_ids: list[int],
    group_names: dict[int, str],
    scan_limit: int,
) -> None:
    """
    Perform a bounded one-level scan.

    This catches sites such as Notre Dame, where project storage may be
    /groups/<PI-name> and the directory's group ID matches one of the user's
    supplemental groups even when the directory name is not identical to the
    Unix group name.
    """
    group_id_set = set(group_ids)

    for root in roots:
        root_path = existing_directory(root)
        if root_path is None or not os.access(root_path, os.R_OK | os.X_OK):
            continue

        role = root_role(root_path)

        try:
            iterator = os.scandir(root_path)
        except OSError:
            continue

        with iterator:
            for index, entry in enumerate(iterator):
                if index >= scan_limit:
                    break

                if entry.name.startswith("."):
                    continue

                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue

                    info = entry.stat(follow_symlinks=True)
                except OSError:
                    continue

                path = os.path.realpath(entry.path)

                owned_by_user = info.st_uid == uid
                owned_by_group = info.st_gid in group_id_set
                writable = can_store(path)

                if not (owned_by_user or owned_by_group or writable):
                    continue

                # Since the final candidate must be writable, read-only directories
                # owned by one of the user's groups are not presented as storage.
                if not writable:
                    continue

                group_name = group_names.get(info.st_gid)

                score = 98 if owned_by_group else 92 if owned_by_user else 88

                merge_candidate(
                    candidates,
                    path,
                    role=role,
                    score=score,
                    source="bounded_root_scan",
                    group_name=group_name,
                    group_path_name=entry.name if group_name else None,
                )


def extract_paths(text: str) -> list[str]:
    paths: list[str] = []

    for match in ABSOLUTE_PATH_RE.finditer(text):
        candidate = match.group(1).rstrip(".,;:)")

        real_path = existing_directory(candidate)
        if real_path:
            paths.append(real_path)

    return sorted(set(paths))


def discover_quota_paths(
    candidates: dict[str, Candidate],
) -> tuple[list[str], list[str]]:
    available_tools: list[str] = []
    successful_commands: list[str] = []

    for tool, command in QUOTA_COMMANDS:
        if not shutil.which(tool):
            continue

        available_tools.append(tool)
        success, output = run_command(command, timeout=5)

        if not success:
            continue

        successful_commands.append(" ".join(command))

        for path in extract_paths(output):
            merge_candidate(
                candidates,
                path,
                role="storage",
                score=75,
                source=f"quota:{tool}",
            )

    for tool in DETECT_ONLY_QUOTA_TOOLS:
        if shutil.which(tool):
            available_tools.append(tool)

    return sorted(set(available_tools)), successful_commands


def discover_temporary_paths(
    candidates: dict[str, Candidate],
) -> None:
    for path in ("/tmp", "/dev/shm"):
        merge_candidate(
            candidates,
            path,
            role="temporary",
            score=20,
            source="well_known_path",
        )


def find_mount(path: str) -> dict[str, Any]:
    success, output = run_command(
        [
            "findmnt",
            "--json",
            "--target",
            path,
            "--output",
            "TARGET,SOURCE,FSTYPE",
        ],
        timeout=5,
    )

    if not success:
        return {
            "mount_target": None,
            "mount_source": None,
            "filesystem_type": None,
        }

    try:
        document = json.loads(output)
        filesystems = document.get("filesystems", [])
        record = filesystems[0] if filesystems else {}
    except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
        record = {}

    return {
        "mount_target": record.get("target"),
        "mount_source": record.get("source"),
        "filesystem_type": record.get("fstype"),
    }


def filesystem_available_bytes(path: str) -> int | None:
    try:
        info = os.statvfs(path)
    except OSError:
        return None

    return info.f_bavail * info.f_frsize


def make_path_pattern(
    path: str,
    username: str,
    candidate: Candidate,
    all_group_names: list[str],
) -> str:
    components = list(Path(path).parts)

    for index, component in enumerate(components):
        if component == username:
            components[index] = "{username}"
            continue

        if (
            candidate.group_path_name
            and component == candidate.group_path_name
        ):
            components[index] = "{group}"
            continue

        for group_name in all_group_names:
            if component in group_aliases(group_name):
                components[index] = "{group}"
                break

    if components and components[0] == "/":
        return "/" + "/".join(components[1:])

    return os.path.join(*components) if components else path


def candidate_to_record(
    candidate: Candidate,
    username: str,
    all_group_names: list[str],
) -> dict[str, Any]:
    mount = find_mount(candidate.path)

    return {
        "role": candidate.role,
        "path": candidate.path,
        "path_pattern": make_path_pattern(
            candidate.path,
            username,
            candidate,
            all_group_names,
        ),
        "group_name": candidate.group_name,
        "discovered_by": sorted(candidate.sources),
        "environment_variables": sorted(candidate.environment_variables),
        "filesystem_type": mount["filesystem_type"],
        "mount_target": mount["mount_target"],
        "writable": True,
        "filesystem_available_bytes": filesystem_available_bytes(
            candidate.path
        ),
    }


def remove_nested_duplicates(
    candidates: list[Candidate],
) -> list[Candidate]:
    """
    Keep the highest-value location when several candidates are nested on the
    same filesystem role.

    Example: keep /users/alice, not /users/alice/current-project.
    """
    selected: list[Candidate] = []

    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.score,
            ROLE_PRIORITY.get(item.role, 99),
            len(Path(item.path).parts),
            item.path,
        ),
    )

    for candidate in ordered:
        duplicate = False

        for existing in selected:
            try:
                same_or_nested = (
                    os.path.commonpath([candidate.path, existing.path])
                    == existing.path
                )
            except ValueError:
                same_or_nested = False

            if same_or_nested and candidate.role == existing.role:
                duplicate = True
                break

        if not duplicate:
            selected.append(candidate)

    return selected


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect a concise list of writable storage locations visible "
            "from the current login node."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("storage-login-measurement.json"),
    )
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help=(
            "Additional root to probe for user or group storage. "
            "May be provided more than once."
        ),
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=2000,
        help="Maximum number of immediate children examined per storage root.",
    )
    parser.add_argument(
        "--max-locations",
        type=int,
        default=10,
        help="Maximum number of storage locations written to the output.",
    )
    parser.add_argument(
        "--no-temp",
        action="store_true",
        help="Do not include /tmp or /dev/shm.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    uid = os.getuid()
    user_record = pwd.getpwuid(uid)
    username = user_record.pw_name
    home_directory = os.path.realpath(user_record.pw_dir)

    group_ids, group_names_by_gid = safe_group_context()
    all_group_names = sorted(set(group_names_by_gid.values()))

    roots = list(dict.fromkeys(DEFAULT_ROOTS + args.root))
    candidates: dict[str, Candidate] = {}

    discover_home(candidates, username, home_directory)
    discover_storage_environment(candidates)
    discover_exact_group_paths(
        candidates,
        roots,
        username,
        group_names_by_gid,
    )
    discover_group_owned_children(
        candidates,
        roots,
        uid,
        group_ids,
        group_names_by_gid,
        max(1, args.scan_limit),
    )
    available_quota_tools, quota_commands_used = discover_quota_paths(
        candidates
    )

    if not args.no_temp:
        discover_temporary_paths(candidates)

    selected = remove_nested_duplicates(list(candidates.values()))
    selected = sorted(
        selected,
        key=lambda item: (
            ROLE_PRIORITY.get(item.role, 99),
            -item.score,
            item.path,
        ),
    )[: max(1, args.max_locations)]

    locations = [
        candidate_to_record(
            candidate,
            username,
            all_group_names,
        )
        for candidate in selected
    ]

    measurement = {
        "schema_version": "0.6",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "evidence_source": "measured",
        "storage": {
            "locations": locations,
            "available_quota_tools": available_quota_tools,
            "quota_commands_used_for_discovery": quota_commands_used,
        },
    }

    args.output.write_text(
        json.dumps(measurement, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {args.output}")
    print(f"Storage locations: {len(locations)}")

    for location in locations:
        role = location["role"]
        path = location["path"]
        print(f"  {role:10s} {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
