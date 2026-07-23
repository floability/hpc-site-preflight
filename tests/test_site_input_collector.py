"""Tests for the structured standalone login-node collector."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "hpc_site_preflight" / "measurements" / "capture.py"
ANVIL_OUTPUT = ROOT / "examples" / "simulate" / "anvil" / "login-measurements.json"


def load_collector() -> ModuleType:
    """Load the standalone script without making scripts a package."""

    spec = importlib.util.spec_from_file_location("collect_site_inputs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_output_has_addressable_site_storage_and_slurm_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = load_collector()
    home = tmp_path / "home" / "testuser"
    scratch = tmp_path / "scratch" / "testuser"
    project = tmp_path / "project" / "group"
    for path in (home, scratch, project):
        path.mkdir(parents=True)

    monkeypatch.setattr(module.getpass, "getuser", lambda: "testuser")
    monkeypatch.setattr(module.os, "getgroups", lambda: [123])
    monkeypatch.setattr(
        module.grp,
        "getgrgid",
        lambda group_id: SimpleNamespace(gr_name="group"),
    )
    outputs = {
        ("hostname", "-f"): "login01.cluster.example.edu",
        ("cat", "/etc/os-release"): 'ID="rocky"\nVERSION_ID="9.4"',
        ("uname", "-r"): "5.14.0",
        ("uname", "-m"): "x86_64",
        ("cat", "/proc/meminfo"): "MemAvailable: 1024 kB",
        ("stat", "-f", "-c", "%T", str(home)): "nfs",
        ("stat", "-f", "-c", "%T", str(tmp_path)): "tmpfs",
        ("stat", "-f", "-c", "%T", str(scratch)): "lustre",
        ("stat", "-f", "-c", "%T", str(project)): "gpfs",
        ("sinfo", "--version"): "slurm 24.05.2",
        (
            "sinfo",
            "-h",
            "-o",
            "%P %D %m %c %G",
        ): "shared* 10 256000 128 (null)\ngpu 4 512000 128 gpu:a100:4",
    }

    def runner(arguments):
        return outputs.get(tuple(arguments))

    def lookup(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in module.SLURM_COMMANDS else None

    result = module.collect_measurements(
        site_name="Example Cluster",
        keywords=["allocation"],
        domain_overrides=[],
        environment={
            "HOME": str(home),
            "TMPDIR": str(tmp_path),
            "SCRATCH": str(scratch),
            "PROJECT": str(project),
        },
        runner=runner,
        executable_lookup=lookup,
    )

    assert result["schema_version"] == "0.6"
    assert result["detected_schedulers"] == ["slurm"]
    assert result["site_facts"]["site_id"] == "example-cluster"
    assert result["site_facts"]["fqdn"] == "login01.cluster.example.edu"
    assert result["site_facts"]["hostname_patterns"] == [
        "*.cluster.example.edu"
    ]
    assert result["site_facts"]["documentation_domains"] == ["example.edu"]
    assert result["site_facts"]["os_id"] == "rocky"
    assert result["site_facts"]["architecture"] == "x86_64"
    assert result["site_facts"]["available_memory_bytes"] == 1048576
    assert "software" not in result["site_facts"]

    assert list(result["storage"]) == ["home", "tmp", "scratch", "project"]
    assert result["storage"]["home"]["observed_path"] == str(home)
    assert result["storage"]["home"]["path_pattern"].endswith("/home/{username}")
    assert result["storage"]["scratch"]["path_pattern"].endswith(
        "/scratch/{username}"
    )
    assert result["storage"]["project"]["path_pattern"].endswith("/project/{group}")
    assert result["storage"]["scratch"]["filesystem_type"] == "lustre"
    assert isinstance(result["storage"]["scratch"]["permissions"], str)

    assert result["htcondor"] is None
    assert result["slurm"]["version"] == "24.05.2"
    assert result["slurm"]["default_partition"] == "shared"
    partitions = {
        partition["name"]: partition for partition in result["slurm"]["partitions"]
    }
    assert partitions["shared"]["node_count"] == 10
    assert partitions["shared"]["memory_mib_per_node"] == 256000
    assert partitions["gpu"]["gpu_count_per_node"] == 4
    assert partitions["gpu"]["gpu_models"] == ["a100"]


def test_missing_storage_and_slurm_remain_null(tmp_path: Path, monkeypatch) -> None:
    module = load_collector()
    monkeypatch.setattr(module.getpass, "getuser", lambda: "testuser")

    def runner(arguments):
        if list(arguments) == ["hostname", "-f"]:
            return "login.site.example.org"
        return None

    result = module.collect_measurements(
        site_name="Example",
        keywords=[],
        domain_overrides=[],
        environment={"HOME": str(tmp_path)},
        runner=runner,
        executable_lookup=lambda command: None,
    )

    assert result["detected_schedulers"] == []
    assert result["slurm"] is None
    assert result["htcondor"] is None
    assert result["storage"]["scratch"]["observed_path"] is None
    assert result["storage"]["scratch"]["path_pattern"] is None
    assert result["storage"]["scratch"]["permissions"] is None


def test_htcondor_object_is_selected_when_commands_exist(tmp_path: Path) -> None:
    module = load_collector()

    def lookup(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in module.HTCONDOR_COMMANDS else None

    result = module.collect_measurements(
        site_name="Example",
        keywords=[],
        domain_overrides=[],
        environment={"HOME": str(tmp_path)},
        runner=lambda arguments: "login.site.example.org"
        if list(arguments) == ["hostname", "-f"]
        else None,
        executable_lookup=lookup,
    )

    assert result["detected_schedulers"] == ["htcondor"]
    assert result["slurm"] is None
    assert result["htcondor"]["submit_command_available"] is True


def test_cli_requires_only_short_site_name() -> None:
    module = load_collector()
    args = module.parse_args(["--short-site-name", "Example"])

    assert args.site_name == "Example"
    assert args.output == Path("login-measurements.json")


def test_real_anvil_output_matches_current_standalone_shape() -> None:
    payload = json.loads(ANVIL_OUTPUT.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "0.6"
    assert payload["site_facts"]["site_name"] == "Anvil"
    assert payload["site_facts"]["os_id"] == "rocky"
    assert payload["site_facts"]["cpu_count"] == 32
    assert "identity" not in payload["site_facts"]
    assert "software" not in payload["site_facts"]
    assert isinstance(payload["slurm"]["partitions"], list)
