"""Bounded login-node storage discovery shared by scheduler collectors."""

from __future__ import annotations

import os
import re
import stat
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

CommandRunner = Callable[[Sequence[str]], str | None]

STORAGE_ENVIRONMENT = re.compile(
    r"(^|_)(SCRATCH|PSCRATCH|WORK|WORKDIR|MEMBERWORK|PROJECT|PROJECTS|"
    r"DATA|STORAGE|SHARED|ARCHIVE|NOBACKUP)(_|$)",
    re.IGNORECASE,
)
DEFAULT_ROOTS = (
    "/groups",
    "/group",
    "/projects",
    "/project",
    "/work",
    "/scratch",
    "/data",
    "/storage",
)


def collect_storage(
    *,
    username: str,
    groups: Sequence[str],
    environment: Mapping[str, str],
    runner: CommandRunner,
    extra_paths: Sequence[str] = (),
    extra_roots: Sequence[str] = (),
) -> dict[str, list[dict[str, Any]]]:
    """Return readable storage from reviewed exact paths without scanning directories."""

    candidates: dict[str, dict[str, Any]] = {}

    def add(
        raw_path: str,
        role: str,
        environment_variable: str | None = None,
    ) -> None:
        expanded = os.path.expandvars(os.path.expanduser(raw_path))
        if not os.path.isabs(expanded):
            return
        path = os.path.realpath(expanded)
        if not os.path.isdir(path) or not os.access(path, os.R_OK | os.X_OK):
            return
        item = candidates.setdefault(
            path,
            {"role": role, "environment_variables": []},
        )
        if environment_variable and environment_variable not in item["environment_variables"]:
            item["environment_variables"].append(environment_variable)

    home = environment.get("HOME")
    if home:
        add(home, "home", "HOME")
    for name, value in environment.items():
        if value and STORAGE_ENVIRONMENT.search(name):
            add(value, _role(name), name)

    for root in [*DEFAULT_ROOTS, *extra_roots]:
        role = _role(Path(root).name)
        add(str(Path(root) / username), role)
        for group in groups:
            for alias in _group_aliases(group):
                add(str(Path(root) / alias), role)

    for value in extra_paths:
        role, separator, path = value.partition("=")
        add(path if separator else value, role if separator else "storage")

    role_counts: dict[str, int] = defaultdict(int)
    locations: list[dict[str, Any]] = []
    for path, candidate in sorted(candidates.items()):
        role = candidate["role"]
        role_counts[role] += 1
        location_id = role if role_counts[role] == 1 else f"{role}-{role_counts[role]}"
        try:
            permissions = stat.filemode(Path(path).stat().st_mode)
        except OSError:
            permissions = None
        locations.append(
            {
                "id": location_id,
                "role": role,
                "environment_variables": sorted(candidate["environment_variables"]),
                "observed_path": path,
                "path_pattern": _path_pattern(path, username, groups),
                "exists": True,
                "readable": os.access(path, os.R_OK),
                "writable": os.access(path, os.W_OK),
                "executable": os.access(path, os.X_OK),
                "permissions": permissions,
                "filesystem_type": runner(["stat", "-f", "-c", "%T", path]),
            }
        )
    return {"locations": locations}


def _role(value: str) -> str:
    """Map a reviewed path hint to a broad storage role."""

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


def _path_pattern(path: str, username: str, groups: Sequence[str]) -> str:
    """Generalize exact user and Unix-group path components."""

    group_names = {
        alias for group in groups for alias in _group_aliases(group)
    }
    replacements = {
        username: "{username}",
        **{group: "{group}" for group in group_names},
    }
    return "/".join(replacements.get(part, part) for part in path.split("/"))


def _group_aliases(group: str) -> set[str]:
    """Return the exact Unix group and a conservative numeric-suffix alias."""

    stripped = re.sub(r"[-_]\d+$", "", group)
    return {group, stripped} if stripped else {group}
