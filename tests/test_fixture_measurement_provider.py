"""Fixture measurement provider tests."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.exceptions import FixtureLoadError, FixtureValidationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.measurements.fixture import FixtureMeasurementProvider
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "examples" / "fixture"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tracker(tmp_path: Path) -> RunTracker:
    return RunTracker(command="test", run_root=tmp_path, quiet=True, run_id="fixture")


@pytest.mark.parametrize("fixture_name", ["anvil", "stampede3", "notre-dame-crc"])
def test_fixture_provider_loads_and_validates_site_pair(
    fixture_name: str, tmp_path: Path
) -> None:
    fixture_dir = FIXTURE_ROOT / fixture_name
    site = SiteInfo.model_validate(_load(fixture_dir / "site-info.json"))
    tracker = _tracker(tmp_path)

    bundle = FixtureMeasurementProvider(fixture_dir / "login-measurements.json").collect(
        site, tracker
    )

    expected = MeasurementBundle.model_validate(_load(fixture_dir / "login-measurements.json"))
    assert bundle == expected
    assert [stage.name for stage in tracker.report.steps] == [
        "fixture_measurement_load",
        "fixture_measurement_validate",
    ]
    assert all(stage.status == "completed" for stage in tracker.report.steps)


def test_fixture_provider_rejects_site_mismatch(tmp_path: Path) -> None:
    fixture_dir = FIXTURE_ROOT / "anvil"
    site = SiteInfo.model_validate(_load(fixture_dir / "site-info.json")).model_copy(
        update={"site_id": "different-site"}
    )
    tracker = _tracker(tmp_path)

    with pytest.raises(FixtureValidationError, match="does not match"):
        FixtureMeasurementProvider(fixture_dir / "login-measurements.json").collect(site, tracker)

    assert [stage.status for stage in tracker.report.steps] == ["completed", "failed"]


def test_fixture_provider_rejects_scheduler_mismatch(tmp_path: Path) -> None:
    fixture_dir = FIXTURE_ROOT / "anvil"
    site = SiteInfo.model_validate(_load(fixture_dir / "site-info.json")).model_copy(
        update={"scheduler": "htcondor"}
    )

    with pytest.raises(FixtureValidationError, match="scheduler"):
        FixtureMeasurementProvider(fixture_dir / "login-measurements.json").collect(
            site, _tracker(tmp_path)
        )


def test_fixture_provider_reports_invalid_json_without_contents(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{invalid", encoding="utf-8")
    site = SiteInfo.model_validate(_load(FIXTURE_ROOT / "anvil" / "site-info.json"))

    with pytest.raises(FixtureLoadError, match="invalid JSON at line 1"):
        FixtureMeasurementProvider(path).collect(site, _tracker(tmp_path))


def test_fixture_provider_rejects_live_measurements(tmp_path: Path) -> None:
    payload = _load(FIXTURE_ROOT / "anvil" / "login-measurements.json")
    payload["source_mode"] = "live"
    payload.pop("fixture_origin")
    path = tmp_path / "live.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    site = SiteInfo.model_validate(_load(FIXTURE_ROOT / "anvil" / "site-info.json"))

    with pytest.raises(FixtureValidationError, match="requires source_mode 'fixture'"):
        FixtureMeasurementProvider(path).collect(site, _tracker(tmp_path))
