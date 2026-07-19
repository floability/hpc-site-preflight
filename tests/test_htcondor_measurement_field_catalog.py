"""Consistency checks for the HTCondor login-node fact catalog."""

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
    "/facts/scheduler/collector_host",
    "/facts/scheduler/pool_version",
    "/facts/scheduler/collector_ads",
    "/facts/scheduler/schedd_ads",
    "/facts/scheduler/execute_machines",
    "/facts/scheduler/static_slot_count",
    "/facts/scheduler/partitionable_slot_count",
    "/facts/scheduler/dynamic_slot_count",
    "/facts/scheduler/raw_slot_classads",
    "/facts/scheduler/raw_slot_classads/*/state",
    "/facts/scheduler/raw_slot_classads/*/activity",
    "/facts/scheduler/raw_slot_classads/*/cpus",
    "/facts/scheduler/raw_slot_classads/*/memory_mib",
    "/facts/scheduler/raw_slot_classads/*/disk_kib",
    "/facts/scheduler/raw_slot_classads/*/gpu_count",
    "/facts/scheduler/raw_slot_classads/*/architecture",
    "/facts/scheduler/raw_slot_classads/*/operating_system",
    "/facts/scheduler/raw_slot_classads/*/grouping_attributes",
    "/facts/scheduler/resource_groups",
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((CATALOG_DIR / name).read_text(encoding="utf-8"))


def test_htcondor_catalog_has_expected_shape() -> None:
    catalog = _load("htcondor.json")
    fields = catalog["fields"]
    paths = [field["path"] for field in fields]

    assert set(catalog) == REQUIRED_TOP_LEVEL_KEYS
    assert catalog["scheduler"] == "htcondor"
    assert catalog["status_catalog"] == "common.json#/statuses"
    assert len(paths) == len(set(paths))
    assert REQUIRED_PATHS <= set(paths)
    assert not any("/partitions" in path for path in paths)


def test_htcondor_fields_use_fixed_reviewed_commands() -> None:
    for field in _load("htcondor.json")["fields"]:
        assert set(field) == REQUIRED_FIELD_KEYS
        assert field["path"].startswith("/facts/scheduler/")
        assert field["method"] in {"fixed_command", "derived", "executable_lookup"}
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


def test_raw_classads_and_derived_resource_groups_are_separate() -> None:
    fields = {field["path"]: field for field in _load("htcondor.json")["fields"]}
    raw = fields["/facts/scheduler/raw_slot_classads"]
    groups = fields["/facts/scheduler/resource_groups"]

    assert raw["method"] == "fixed_command"
    assert groups["method"] == "derived"
    assert "never partitions" in groups["description"]
    assert "slurm" not in json.dumps(fields).lower()
