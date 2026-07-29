"""Consistency checks for the Slurm login-node fact catalog."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_DIR = ROOT / "schemas" / "measurement-fields"

REQUIRED_TOP_LEVEL_KEYS = {
    "schema_version",
    "catalog_id",
    "title",
    "scheduler",
    "status_catalog",
    "fields",
}
REQUIRED_FIELD_KEYS = {
    "path",
    "type",
    "description",
    "method",
    "command_id",
    "command",
    "output_concept",
    "requires_write",
    "optional",
}
REQUIRED_PATHS = {
    "/facts/scheduler/partitions",
    "/facts/scheduler/partitions/*/node_states",
    "/facts/scheduler/node_states",
    "/facts/scheduler/node_shapes",
    "/facts/scheduler/node_shapes/*/cpus",
    "/facts/scheduler/node_shapes/*/memory_mib",
    "/facts/scheduler/node_shapes/*/temporary_disk_mib",
    "/facts/scheduler/node_shapes/*/features",
    "/facts/scheduler/node_shapes/*/gres",
    "/facts/scheduler/node_shapes/*/gpu_count",
    "/facts/scheduler/node_shapes/*/gpu_models",
    "/facts/scheduler/visible_association_limits",
    "/facts/scheduler/reservations",
    "/facts/scheduler/visible_configuration",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((CATALOG_DIR / name).read_text(encoding="utf-8"))


def test_slurm_catalog_has_expected_shape() -> None:
    catalog = _load("slurm.json")
    fields = catalog["fields"]
    paths = [field["path"] for field in fields]

    assert set(catalog) == REQUIRED_TOP_LEVEL_KEYS
    assert catalog["scheduler"] == "slurm"
    assert catalog["status_catalog"] == "common.json#/statuses"
    assert 20 <= len(fields) <= 30
    assert len(paths) == len(set(paths))
    assert REQUIRED_PATHS <= set(paths)
    assert "/facts/scheduler/partitions" != "/facts/scheduler/node_shapes"


def test_slurm_fields_use_fixed_reviewed_commands() -> None:
    for field in _load("slurm.json")["fields"]:
        assert set(field) == REQUIRED_FIELD_KEYS
        assert field["path"].startswith("/facts/scheduler/")
        assert field["method"] in {"fixed_command", "derived"}
        assert isinstance(field["description"], str) and field["description"]
        assert isinstance(field["command_id"], str) and field["command_id"]
        assert isinstance(field["output_concept"], str) and field["output_concept"]
        assert field["requires_write"] is False
        assert isinstance(field["optional"], bool)

        if field["method"] == "fixed_command":
            assert isinstance(field["command"], list) and field["command"]
            assert all(isinstance(argument, str) for argument in field["command"])
            assert field["command"][0] not in {"bash", "sh", "zsh"}
            assert all("$" not in argument for argument in field["command"])
        else:
            assert field["command"] is None


def test_slurm_catalog_preserves_visibility_and_policy_boundaries() -> None:
    catalog = _load("slurm.json")
    fields = {field["path"]: field for field in catalog["fields"]}
    statuses = _load("common.json")["statuses"]

    walltime = fields["/facts/scheduler/partitions/*/maximum_walltime_seconds"]
    assert walltime["command"] == ["sinfo", "-h", "-o", "%P %l"]
    assert "-1 meaning unlimited" in walltime["description"]
    assert "Users={username}" in fields["/facts/scheduler/visible_association_limits"]["command"]
    assert fields["/facts/scheduler/reservations"]["command"] == [
        "scontrol",
        "--oneliner",
        "show",
        "reservations",
    ]
    assert {"unavailable", "hidden", "permission_denied", "command_unavailable"} <= set(
        statuses
    )
