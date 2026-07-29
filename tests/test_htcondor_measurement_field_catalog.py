"""Consistency checks for the compact HTCondor login-node fact catalog."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "schemas" / "measurement-fields" / "htcondor.json"
REQUIRED_FIELD_KEYS = {
    "path",
    "type",
    "description",
    "method",
    "command_id",
    "command",
    "requires_write",
    "optional",
}
REQUIRED_PATHS = {
    "/htcondor/version",
    "/htcondor/collector_host",
    "/htcondor/submit_command_available",
    "/htcondor/file_transfer_supported",
    "/htcondor/pool_totals/machine_count",
    "/htcondor/pool_totals/cpu_cores",
    "/htcondor/pool_totals/memory_mib",
    "/htcondor/pool_totals/advertised_gpus",
    "/htcondor/cpu_groups",
    "/htcondor/gpu_groups",
}


def _load() -> dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_htcondor_catalog_matches_the_compact_measurement() -> None:
    catalog = _load()
    fields = catalog["fields"]
    paths = [field["path"] for field in fields]

    assert catalog["scheduler"] == "htcondor"
    assert len(paths) == len(set(paths))
    assert REQUIRED_PATHS <= set(paths)
    assert not any("partition" in path for path in paths)


def test_htcondor_catalog_uses_one_reviewed_classad_query() -> None:
    fixed_commands: list[list[str]] = []
    for field in _load()["fields"]:
        assert set(field) == REQUIRED_FIELD_KEYS
        assert field["method"] in {"fixed_command", "derived", "executable_lookup"}
        assert field["requires_write"] is False
        if field["method"] == "fixed_command":
            assert isinstance(field["command"], list) and field["command"]
            fixed_commands.append(field["command"])
        else:
            assert field["command"] is None

    status_queries = [
        command for command in fixed_commands if command[0] == "condor_status"
    ]
    assert len(status_queries) == 1
    assert "-gpus" not in status_queries[0]
