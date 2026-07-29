"""Checks for the three laptop-development site simulations."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hpc_site_preflight.measurements.base import MeasurementBundle

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"
SITE_IDS = {
    "anvil": ("anvil", "slurm"),
    "stampede3": ("tacc-stampede3", "slurm"),
    "notre-dame-crc": ("notre-dame-crc", "htcondor"),
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("simulation_name", SITE_IDS)
def test_site_simulation_validates(simulation_name: str) -> None:
    expected_site_id, expected_scheduler = SITE_IDS[simulation_name]
    measurement_payload = _load(SIMULATE_ROOT / simulation_name / "login-measurements.json")
    measurements = MeasurementBundle.model_validate(measurement_payload)

    assert measurements.site_id == expected_site_id
    assert measurements.scheduler_type == expected_scheduler
    expected_source = "measured" if simulation_name == "anvil" else "simulated"
    assert measurements.evidence_source == expected_source


def test_anvil_measurement_leaves_uncollected_visible_walltime_empty() -> None:
    bundle = MeasurementBundle.model_validate(
        _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    )
    assert bundle.slurm is not None
    partitions = {item.name: item for item in bundle.slurm.partitions}
    walltime = partitions["shared"].visible_walltime_limit

    assert walltime is None


def test_stampede3_simulation_uses_the_slurm_structure() -> None:
    bundle = MeasurementBundle.model_validate(
        _load(SIMULATE_ROOT / "stampede3" / "login-measurements.json")
    )
    assert bundle.slurm is not None
    assert bundle.slurm.partitions
    assert all(item.cpus_per_node for item in bundle.slurm.partitions)


def test_notre_dame_simulation_uses_classads_and_resource_groups() -> None:
    bundle = MeasurementBundle.model_validate(
        _load(SIMULATE_ROOT / "notre-dame-crc" / "login-measurements.json")
    )
    assert bundle.slurm is None
    assert bundle.htcondor is not None
    assert bundle.htcondor.pool_totals.machine_count >= 0
    assert isinstance(bundle.htcondor.cpu_groups, list)
    assert isinstance(bundle.htcondor.gpu_groups, list)


def test_scheduler_models_reject_cross_scheduler_fields() -> None:
    slurm_payload = _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    slurm_payload["detected_schedulers"] = ["htcondor"]
    with pytest.raises(ValidationError, match="must agree"):
        MeasurementBundle.model_validate(slurm_payload)

    htcondor_payload = _load(SIMULATE_ROOT / "notre-dame-crc" / "login-measurements.json")
    htcondor_payload["detected_schedulers"] = ["slurm"]
    with pytest.raises(ValidationError, match="must agree"):
        MeasurementBundle.model_validate(htcondor_payload)
