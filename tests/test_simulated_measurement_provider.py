"""Simulated measurement provider tests."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.exceptions import SimulationLoadError, SimulationValidationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.measurements.simulated import SimulatedMeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tracker(tmp_path: Path) -> RunTracker:
    return RunTracker(command="test", run_root=tmp_path, quiet=True, run_id="simulate")


@pytest.mark.parametrize("simulation_name", ["anvil", "stampede3", "notre-dame-crc"])
def test_simulated_provider_loads_and_validates_site_pair(
    simulation_name: str, tmp_path: Path
) -> None:
    simulation_dir = SIMULATE_ROOT / simulation_name
    site = SiteInfo.model_validate(_load(simulation_dir / "site-info.json"))
    tracker = _tracker(tmp_path)

    bundle = SimulatedMeasurementProvider(simulation_dir / "login-measurements.json").collect(
        site, tracker
    )

    expected = MeasurementBundle.model_validate(_load(simulation_dir / "login-measurements.json"))
    assert bundle == expected
    assert [stage.name for stage in tracker.report.steps] == [
        "simulated_measurement_load",
        "simulated_measurement_validate",
    ]
    assert all(stage.status == "completed" for stage in tracker.report.steps)


def test_simulated_provider_rejects_site_mismatch(tmp_path: Path) -> None:
    simulation_dir = SIMULATE_ROOT / "anvil"
    site = SiteInfo.model_validate(_load(simulation_dir / "site-info.json")).model_copy(
        update={"site_id": "different-site"}
    )
    tracker = _tracker(tmp_path)

    with pytest.raises(SimulationValidationError, match="does not match"):
        provider = SimulatedMeasurementProvider(simulation_dir / "login-measurements.json")
        provider.collect(site, tracker)

    assert [stage.status for stage in tracker.report.steps] == ["completed", "failed"]


def test_simulated_provider_rejects_scheduler_mismatch(tmp_path: Path) -> None:
    simulation_dir = SIMULATE_ROOT / "anvil"
    site = SiteInfo.model_validate(_load(simulation_dir / "site-info.json")).model_copy(
        update={"scheduler": "htcondor"}
    )

    with pytest.raises(SimulationValidationError, match="scheduler"):
        SimulatedMeasurementProvider(simulation_dir / "login-measurements.json").collect(
            site, _tracker(tmp_path)
        )


def test_simulated_provider_reports_invalid_json_without_contents(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{invalid", encoding="utf-8")
    site = SiteInfo.model_validate(_load(SIMULATE_ROOT / "anvil" / "site-info.json"))

    with pytest.raises(SimulationLoadError, match="invalid JSON at line 1"):
        SimulatedMeasurementProvider(path).collect(site, _tracker(tmp_path))


def test_simulated_provider_rejects_measured_evidence(tmp_path: Path) -> None:
    payload = _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    payload["evidence_source"] = "measured"
    path = tmp_path / "measured.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    site = SiteInfo.model_validate(_load(SIMULATE_ROOT / "anvil" / "site-info.json"))

    with pytest.raises(SimulationValidationError, match="requires evidence_source 'simulated'"):
        SimulatedMeasurementProvider(path).collect(site, _tracker(tmp_path))
