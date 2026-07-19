"""Typed contract smoke tests using fixture examples."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementObservation
from hpc_site_preflight.probes.base import PilotResultBundle
from hpc_site_preflight.site_info.models import SiteInfo

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_example_site_info_validates() -> None:
    model = SiteInfo.model_validate(_load("examples/fixture/anvil/site-info.json"))
    assert model.site_id == "purdue-anvil"
    assert model.scheduler == "slurm"


def test_example_measurements_validate() -> None:
    model = MeasurementBundle.model_validate(
        _load("examples/fixture/anvil/login-measurements.json")
    )
    assert model.source_mode == "fixture"
    assert model.fixture_origin == "illustrative"
    assert model.scheduler_type == "slurm"


def test_measurement_bundle_supports_both_scheduler_types() -> None:
    payload = _load("examples/fixture/anvil/login-measurements.json")
    payload["scheduler_type"] = "htcondor"
    payload["scheduler"] = []

    model = MeasurementBundle.model_validate(payload)
    assert model.scheduler_type == "htcondor"


def test_measurement_json_has_at_most_two_object_layers() -> None:
    def object_depth(value: object) -> int:
        if isinstance(value, dict):
            return 1 + max((object_depth(item) for item in value.values()), default=0)
        if isinstance(value, list):
            return max((object_depth(item) for item in value), default=0)
        return 0

    payload = _load("examples/fixture/anvil/login-measurements.json")
    assert object_depth(payload) <= 2


def test_example_pilot_results_validate() -> None:
    model = PilotResultBundle.model_validate(_load("examples/fixture/anvil/pilot-results.json"))
    assert model.site_id == "purdue-anvil"
    assert model.fixture_origin == "illustrative"


@pytest.mark.parametrize("model", [MeasurementBundle, PilotResultBundle])
def test_fixture_evidence_requires_an_origin(
    model: type[MeasurementBundle | PilotResultBundle],
) -> None:
    payload = {"site_id": "example", "mode": "fixture"}
    if model is MeasurementBundle:
        payload = {
            "schema_version": "0.1",
            "site_id": "example",
            "scheduler_type": "slurm",
            "collected_at": "2026-07-19T12:00:00Z",
            "source_mode": "fixture",
            "collector_version": "test",
        }

    with pytest.raises(ValidationError, match="fixture_origin is required"):
        model.model_validate(payload)


@pytest.mark.parametrize("model", [MeasurementBundle, PilotResultBundle])
def test_live_evidence_rejects_a_fixture_origin(
    model: type[MeasurementBundle | PilotResultBundle],
) -> None:
    payload = {
        "site_id": "example",
        "mode": "live",
        "fixture_origin": "captured",
    }
    if model is MeasurementBundle:
        payload = {
            "schema_version": "0.1",
            "site_id": "example",
            "scheduler_type": "htcondor",
            "collected_at": "2026-07-19T12:00:00Z",
            "source_mode": "live",
            "fixture_origin": "captured",
            "collector_version": "test",
        }

    with pytest.raises(ValidationError, match="fixture_origin must be omitted"):
        model.model_validate(payload)


@pytest.mark.parametrize("value", [False, 0, []])
def test_observed_false_zero_and_empty_are_values(value: object) -> None:
    observation = MeasurementObservation.model_validate(
        {
            "path": "/facts/example",
            "status": "observed",
            "value": value,
            "observed_at": "2026-07-19T12:00:00Z",
            "method": "derived",
            "command_id": "example",
        }
    )
    assert observation.value == value


def test_unavailable_value_is_null() -> None:
    observation = MeasurementObservation.model_validate(
        {
            "path": "/facts/example",
            "status": "permission_denied",
            "value": None,
            "observed_at": "2026-07-19T12:00:00Z",
            "method": "fixed_command",
            "command_id": "example",
        }
    )
    assert observation.value is None

    with pytest.raises(ValidationError, match="unobserved measurements must use a null value"):
        MeasurementObservation.model_validate(
            {
                "path": "/facts/example",
                "status": "unavailable",
                "value": False,
                "observed_at": "2026-07-19T12:00:00Z",
                "method": "fixed_command",
                "command_id": "example",
            }
        )


def test_measurement_values_do_not_allow_nested_objects() -> None:
    with pytest.raises(ValidationError):
        MeasurementObservation.model_validate(
            {
                "path": "/facts/example",
                "status": "observed",
                "value": {"nested": "value"},
                "observed_at": "2026-07-19T12:00:00Z",
                "method": "derived",
                "command_id": "example",
            }
        )
