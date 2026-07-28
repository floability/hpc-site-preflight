"""Offline tests for the package-level Slurm pilot subsystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from hpc_site_preflight.probes.base import PilotInputs, PilotResultBundle
from hpc_site_preflight.probes.network import COMPUTE_PROGRAM, reconcile_attempts
from hpc_site_preflight.probes.slurm import SlurmPilot, _bash_job


def test_compute_program_uses_only_standard_library_python() -> None:
    compile(COMPUTE_PROGRAM, "compute-pilot.py", "exec")


def test_bash_runs_storage_before_python(tmp_path: Path) -> None:
    job = _bash_job(
        run_dir=tmp_path,
        compute_program=tmp_path / "compute-pilot.py",
        storage={"home": "/home/example"},
        partition=None,
        account=None,
        role="primary",
    )

    assert "check_storage home /home/example" in job
    assert job.index("check_storage home") < job.index('PYTHON=""')
    assert "#SBATCH --partition=" not in job
    assert "#SBATCH --account=" not in job


def test_optional_slurm_inputs_are_reviewed_directives(tmp_path: Path) -> None:
    job = _bash_job(
        run_dir=tmp_path,
        compute_program=tmp_path / "compute-pilot.py",
        storage={},
        partition="shared",
        account="example-account",
        role="peer",
        exclude="a001",
    )

    assert "#SBATCH --partition=shared" in job
    assert "#SBATCH --account=example-account" in job
    assert "#SBATCH --exclude=a001" in job


def test_reconciliation_requires_both_endpoints() -> None:
    attempts = reconcile_attempts(
        [
            {"port": 9000, "status": "passed", "error": None},
            {"port": 9100, "status": "failed", "error": "timeout"},
        ],
        [
            {"port": 9000, "status": "passed", "error": None},
            {"port": 9100, "status": "passed", "error": None},
        ],
    )

    assert attempts[0]["status"] == "passed"
    assert attempts[1]["status"] == "failed"


def test_missing_sbatch_returns_flat_failed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hpc_site_preflight.probes.slurm.shutil.which", lambda _: None)
    output = tmp_path / "pilot-result.json"
    inputs = PilotInputs(
        site_id="example",
        scheduler="slurm",
        login_host="login.example.edu",
        storage={"home": "/home/example"},
        output=output,
        runs_dir=tmp_path / "pilot-runs",
    )

    result = SlurmPilot().run(inputs)

    assert result.status == "failed"
    assert result.storage_results is None
    assert result.login_compute_attempts is None
    assert output.exists()
    assert PilotResultBundle.model_validate_json(
        output.read_text(encoding="utf-8")
    ).errors


def test_flat_result_ignores_unknown_and_missing_optional_fields() -> None:
    result = PilotResultBundle.model_validate(
        {
            "schema_version": "0.1",
            "site_id": "anvil",
            "scheduler": "slurm",
            "evidence_source": "simulated",
            "pilot_id": "recorded",
            "future_field": {"ignored": True},
        }
    )

    assert result.storage_results is None
    assert result.login_compute_attempts is None
