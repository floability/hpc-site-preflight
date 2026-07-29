"""Tests for the finalized HTCondor login-measurement collector."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from hpc_site_preflight.measurements.htcondor import ATTRIBUTES, collect_htcondor
from hpc_site_preflight.measurements.storage import collect_storage

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_htcondor_measurement.py"


def _ads() -> str:
    return json.dumps(
        [
            {
                "Machine": "cpu-a.example.edu",
                "PartitionableSlot": True,
                "TotalCpus": 24,
                "TotalMemory": 64000,
                "HasFileTransfer": True,
            },
            {
                "Machine": "gpu-a.example.edu",
                "PartitionableSlot": True,
                "TotalCpus": 24,
                "TotalMemory": 128000,
                "TotalGPUs": 4,
                "HasFileTransfer": True,
            },
            {
                "Machine": "cpu-b.example.edu",
                "PartitionableSlot": True,
                "TotalCpus": 64,
                "TotalMemory": 256000,
                "HasFileTransfer": True,
            },
        ]
    )


def _runner(arguments: list[str]) -> str | None:
    if arguments and arguments[0] == "condor_status":
        return _ads()
    if arguments == ["condor_version"]:
        return "$CondorVersion: 24.0.12 $"
    if arguments == ["condor_config_val", "COLLECTOR_HOST"]:
        return "collector.example.edu"
    return None


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("new_condor_collector", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_collector_builds_cpu_gpu_groups_from_one_query() -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str]) -> str | None:
        calls.append(list(arguments))
        return _runner(list(arguments))

    result = collect_htcondor(
        runner,
        ["condor_status", "condor_submit", "condor_q"],
    )

    assert result["version"] == "24.0.12"
    assert result["pool_totals"] == {
        "machine_count": 3,
        "cpu_cores": 112,
        "memory_mib": 448000,
        "advertised_gpus": 4,
    }
    assert result["cpu_groups"][0]["cpu_cores_per_machine"] == 24
    assert result["cpu_groups"][0]["memory_mib_min"] == 64000
    assert result["cpu_groups"][0]["memory_mib_max"] == 128000
    assert result["gpu_groups"] == [
        {
            "gpu_count_per_machine": 4,
            "cpu_cores_per_machine": 24,
            "machine_count": 1,
            "memory_mib_min": 128000,
            "memory_mib_max": 128000,
            "example_machines": ["gpu-a.example.edu"],
        }
    ]
    status_calls = [call for call in calls if call and call[0] == "condor_status"]
    assert len(status_calls) == 1
    assert status_calls[0][-1] == ATTRIBUTES
    assert "-gpus" not in status_calls[0]


def test_standalone_script_uses_the_same_resource_shape(monkeypatch) -> None:
    module = _load_script()
    monkeypatch.setattr(module, "run_command", _runner)
    monkeypatch.setattr(
        module.shutil,
        "which",
        lambda command: f"/usr/bin/{command}",
    )

    result = module.summarize_condor(None)

    assert result["pool_totals"]["machine_count"] == 3
    assert result["cpu_groups"][0]["cpu_cores_per_machine"] == 24
    assert result["gpu_groups"][0]["gpu_count_per_machine"] == 4


def test_storage_is_addressable_and_excludes_temporary_paths(tmp_path: Path) -> None:
    home = tmp_path / "home" / "user"
    scratch = tmp_path / "scratch" / "user"
    temporary = tmp_path / "tmp"
    project_root = tmp_path / "groups"
    project = project_root / "dthain"
    for path in (home, scratch, temporary, project):
        path.mkdir(parents=True)

    result = collect_storage(
        username="user",
        groups=["dthain-1"],
        environment={
            "HOME": str(home),
            "SCRATCH": str(scratch),
            "TMPDIR": str(temporary),
        },
        runner=lambda arguments: "testfs",
        extra_roots=[str(project_root)],
    )
    locations = {item["id"]: item for item in result["locations"]}

    assert set(locations) == {"home", "project", "scratch"}
    assert locations["scratch"]["path_pattern"].endswith("/scratch/{username}")
    assert locations["project"]["path_pattern"].endswith("/groups/{group}")
    assert all(item["observed_path"] != str(temporary) for item in locations.values())
