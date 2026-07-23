"""Simulated measurement provider tests."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.exceptions import SimulationLoadError, SimulationValidationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.measurements.simulated import SimulatedMeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tracker(tmp_path: Path) -> RunTracker:
    return RunTracker(command="test", run_root=tmp_path, quiet=True, run_id="simulate")


@pytest.mark.parametrize("simulation_name", ["anvil", "stampede3", "notre-dame-crc"])
def test_simulated_provider_loads_and_validates_measurements(
    simulation_name: str, tmp_path: Path
) -> None:
    simulation_dir = SIMULATE_ROOT / simulation_name
    tracker = _tracker(tmp_path)

    bundle = SimulatedMeasurementProvider(
        simulation_dir / "login-measurements.json"
    ).collect(tracker)

    expected = MeasurementBundle.model_validate(_load(simulation_dir / "login-measurements.json"))
    assert bundle == expected
    assert [stage.name for stage in tracker.report.steps] == [
        "simulated_measurement_load",
        "simulated_measurement_validate",
    ]
    assert all(stage.status == "completed" for stage in tracker.report.steps)


def test_simulated_provider_rejects_scheduler_mismatch(tmp_path: Path) -> None:
    payload = _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    payload["detected_schedulers"] = ["htcondor"]
    path = tmp_path / "mismatch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SimulationValidationError, match="contract validation"):
        SimulatedMeasurementProvider(path).collect(_tracker(tmp_path))


def test_simulated_provider_reports_invalid_json_without_contents(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{invalid", encoding="utf-8")

    with pytest.raises(SimulationLoadError, match="invalid JSON at line 1"):
        SimulatedMeasurementProvider(path).collect(_tracker(tmp_path))


def test_simulated_provider_accepts_measured_evidence(tmp_path: Path) -> None:
    payload = _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    payload["evidence_source"] = "measured"
    path = tmp_path / "measured.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    bundle = SimulatedMeasurementProvider(path).collect(_tracker(tmp_path))
    assert bundle.evidence_source == "measured"
