"""Adapt a Floability backpack and command to normalized requirements."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Literal, cast

import yaml  # type: ignore[import-untyped]

from hpc_site_preflight.backpack.models import BackpackRequirements, ResourceRequirements
from hpc_site_preflight.exceptions import ConfigurationError

_VALUE_OPTIONS = {
    "--backpack",
    "--batch-options",
    "--batch-type",
    "--compute-spec",
    "--cores-per-worker",
    "--manager-ports",
    "--worker-transfer-ports",
    "--workers",
}


def load_backpack(path: Path, command: str) -> BackpackRequirements:
    """Return normalized requirements from a backpack path and Floability command."""

    backpack_path = path.expanduser().resolve()
    if not backpack_path.is_dir():
        raise ConfigurationError(f"Backpack directory does not exist: {path}")

    arguments = _parse_command(command)
    options = _command_options(arguments)
    compute_path = _compute_path(backpack_path, options.get("--compute-spec"))
    config = _load_compute_config(compute_path)

    workers = _optional_int(options.get("--workers"), "--workers")
    cores = _optional_int(options.get("--cores-per-worker"), "--cores-per-worker")
    manager_ports = _ports(options.get("--manager-ports", "9123,9150"))
    transfer_range = options.get("--worker-transfer-ports")
    batch_type = options.get("--batch-type")
    requested_scheduler = None if batch_type in {None, "local"} else batch_type
    if requested_scheduler not in {None, "slurm", "condor"}:
        raise ConfigurationError(
            "Initial preflight supports Floability batch types slurm and condor."
        )

    resources = ResourceRequirements(
        minimum_workers=workers if workers is not None else _integer(config, "min-workers", 1),
        maximum_workers=workers if workers is not None else _integer(config, "max-workers", 5),
        cores_per_worker=cores if cores is not None else _integer(config, "cores", 1),
        gpus_per_worker=_integer(config, "gpus", 0),
        memory_mb_per_worker=_optional_config_integer(config, "memory"),
        disk_mb_per_worker=_optional_config_integer(config, "disk"),
    )
    if resources.minimum_workers > resources.maximum_workers:
        raise ConfigurationError("min-workers cannot exceed max-workers in compute.yml.")

    return BackpackRequirements(
        backpack_id=backpack_path.name,
        action=_action(arguments),
        original_command=arguments,
        requested_scheduler=cast(Literal["slurm", "condor"] | None, requested_scheduler),
        batch_options=options.get("--batch-options"),
        condor_requirements=_optional_string(config, "condor-requirements"),
        manager_ports=manager_ports,
        worker_transfer_port_range=transfer_range,
        resources=resources,
        requires_compute_compute_network=transfer_range is not None,
    )


def _parse_command(command: str) -> list[str]:
    """Split but never execute one supported Floability invocation."""

    try:
        arguments = shlex.split(command)
    except ValueError as exc:
        raise ConfigurationError("Floability command has invalid shell quoting.") from exc
    if len(arguments) < 2 or Path(arguments[0]).name != "floability":
        raise ConfigurationError("Command must begin with 'floability'.")
    _action(arguments)
    return arguments


def _action(arguments: list[str]) -> Literal["run", "execute", "workers start"]:
    if arguments[1] == "run":
        return "run"
    if arguments[1] == "execute":
        return "execute"
    if arguments[1:3] == ["workers", "start"]:
        return "workers start"
    raise ConfigurationError(
        "Preflight supports 'floability run', 'floability execute', and 'floability workers start'."
    )


def _command_options(arguments: list[str]) -> dict[str, str]:
    """Read only the reviewed Floability options used by the adapter."""

    result: dict[str, str] = {}
    index = 2 if arguments[1] != "workers" else 3
    while index < len(arguments):
        token = arguments[index]
        name, separator, inline_value = token.partition("=")
        if name not in _VALUE_OPTIONS:
            index += 1
            continue
        if separator:
            result[name] = inline_value
            index += 1
            continue
        if index + 1 >= len(arguments):
            raise ConfigurationError(f"{name} requires a value.")
        result[name] = arguments[index + 1]
        index += 2
    return result


def _compute_path(backpack: Path, supplied: str | None) -> Path:
    if supplied is None:
        return backpack / "compute.yml"
    candidate = Path(supplied).expanduser()
    if candidate.is_absolute() or candidate.exists():
        return candidate.resolve()
    return (backpack / candidate).resolve()


def _load_compute_config(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Could not read Floability compute spec: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in Floability compute spec: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("vine_factory_config"), dict):
        raise ConfigurationError("compute.yml must contain a vine_factory_config mapping.")
    return cast(dict[str, Any], payload["vine_factory_config"])


def _integer(config: dict[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"vine_factory_config.{key} must be an integer.")
    return int(value)


def _optional_config_integer(config: dict[str, Any], key: str) -> int | None:
    return None if key not in config else _integer(config, key, 0)


def _optional_string(config: dict[str, Any], key: str) -> str | None:
    value = config.get(key)
    if value is not None and not isinstance(value, str):
        raise ConfigurationError(f"vine_factory_config.{key} must be a string.")
    return value


def _optional_int(value: str | None, option: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{option} must be an integer.") from exc
    if parsed < 1:
        raise ConfigurationError(f"{option} must be at least 1.")
    return parsed


def _ports(value: str) -> list[int]:
    try:
        ports = [int(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise ConfigurationError("--manager-ports must contain comma-separated integers.") from exc
    if len(ports) != 2 or any(port < 1 or port > 65535 for port in ports):
        raise ConfigurationError("--manager-ports must contain two valid TCP ports.")
    return ports
