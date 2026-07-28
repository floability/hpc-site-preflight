"""Offline tests for the standalone Notre Dame HTCondor pilot."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "notre_dame_htcondor_pilot.py"


def load_pilot() -> ModuleType:
    """Load the standalone script as a test module."""

    spec = importlib.util.spec_from_file_location("notre_dame_pilot", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_generated_compute_program_compiles() -> None:
    pilot = load_pilot()

    compile(pilot.COMPUTE_PROGRAM, "compute-pilot.py", "exec")


def test_default_ports_cover_four_windows() -> None:
    pilot = load_pilot()

    ports = pilot.select_ports(9000, 8, random_source=random.Random(7))

    assert len(ports) == 8
    assert ports[0] == 9000
    assert len(set(ports)) == 8
    for index in range(4):
        low = 9000 + index * 1000
        assert all(
            low <= port <= low + 999
            for port in ports[index * 2 : index * 2 + 2]
        )


def test_primary_submit_uses_shared_files_and_small_requests(
    tmp_path: Path,
) -> None:
    pilot = load_pilot()

    submit = pilot.submit_description(
        run_dir=tmp_path,
        executable=tmp_path / "primary-job.sh",
        role="primary",
        request_cpus=1,
        request_memory="256MB",
        request_disk="10MB",
    )

    assert "universe = vanilla" in submit
    assert "should_transfer_files = NO" in submit
    assert "request_cpus = 1" in submit
    assert "requirements =" not in submit
    assert submit.endswith("queue 1\n")


def test_peer_submit_excludes_primary_machine(tmp_path: Path) -> None:
    pilot = load_pilot()

    submit = pilot.submit_description(
        run_dir=tmp_path,
        executable=tmp_path / "peer-job.sh",
        role="peer",
        request_cpus=1,
        request_memory="256MB",
        request_disk="10MB",
        exclude_machine="node01.crc.nd.edu",
    )

    assert 'requirements = (Machine =!= "node01.crc.nd.edu")' in submit


def test_machine_constraint_rejects_classad_injection(tmp_path: Path) -> None:
    pilot = load_pilot()

    with pytest.raises(ValueError, match="unsupported characters"):
        pilot.submit_description(
            run_dir=tmp_path,
            executable=tmp_path / "peer-job.sh",
            role="peer",
            request_cpus=1,
            request_memory="256MB",
            request_disk="10MB",
            exclude_machine='node") || TRUE',
        )


def test_bash_job_checks_storage_before_python(tmp_path: Path) -> None:
    pilot = load_pilot()

    job = pilot.bash_job(
        run_dir=tmp_path,
        compute_program=tmp_path / "compute-pilot.py",
        preferred_python=Path("/usr/bin/python3"),
        storage={"home": "/users/example"},
        role="primary",
    )

    assert "check_storage home /users/example" in job
    assert job.index("check_storage home") < job.index('PYTHON=""')
    assert "--role primary" in job


def test_dry_run_saves_reviewable_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pilot = load_pilot()
    runs_dir = tmp_path / "pilot-runs"

    exit_code = pilot.main(
        [
            "--dry-run",
            "--runs-dir",
            str(runs_dir),
            "--storage",
            "home=/users/example",
        ]
    )

    assert exit_code == 0
    run_directories = list(runs_dir.iterdir())
    assert len(run_directories) == 1
    run_dir = run_directories[0]
    for name in (
        "pilot-config.json",
        "compute-pilot.py",
        "primary-job.sh",
        "peer-job.sh",
        "primary.submit",
        "peer.submit",
    ):
        assert (run_dir / name).exists()
    assert "Saved peer submit file:" in capsys.readouterr().out


def test_reconciliation_requires_matching_ports_and_distinct_hosts() -> None:
    pilot = load_pilot()
    manager = {
        "fqdn": "node01.crc.nd.edu",
        "attempts": [
            {"port": 9000, "status": "passed"},
            {"port": 9100, "status": "failed"},
        ],
    }
    peer = {
        "fqdn": "node02.crc.nd.edu",
        "attempts": [
            {"port": 9000, "status": "passed"},
            {"port": 9100, "status": "passed"},
        ],
    }

    result = pilot.reconciled_network(manager, peer, require_distinct=True)

    assert result["verified_ports"] == [9000]
    assert result["distinct_nodes"] is True
    assert result["successful"] is True
