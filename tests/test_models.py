"""Typed contract smoke tests using simulated examples."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hpc_site_preflight.measurements.base import MeasurementBundle, MeasurementObservation
from hpc_site_preflight.probes.base import PilotResultBundle

ROOT = Path(__file__).resolve().parents[1]


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_example_measurements_validate() -> None:
    model = MeasurementBundle.model_validate(
        _load("examples/simulate/anvil/login-measurements.json")
    )
    assert model.evidence_source == "measured"
    assert model.scheduler_type == "slurm"
    assert model.partition_names == {
        "wholenode",
        "standard",
        "shared",
        "wide",
        "highmem",
        "debug",
        "gpu",
        "ai",
        "gpu-debug",
        "profiling",
    }
    assert model.schema_version == "0.6"
    assert model.storage_names == {"home", "tmp", "project", "scratch"}


def test_measurement_bundle_supports_both_scheduler_types() -> None:
    payload = _load("examples/simulate/notre-dame-crc/login-measurements.json")

    model = MeasurementBundle.model_validate(payload)
    assert model.scheduler_type == "htcondor"


def test_example_pilot_results_validate() -> None:
    model = PilotResultBundle.model_validate(
        {
            "schema_version": "0.1",
            "site_id": "anvil",
            "scheduler": "slurm",
            "evidence_source": "simulated",
            "pilot_id": "pilot-test",
        }
    )
    assert model.site_id == "anvil"
    assert model.evidence_source == "simulated"


def test_measurement_evidence_source_is_simulated_or_measured() -> None:
    payload = _load("examples/simulate/anvil/login-measurements.json")
    payload["evidence_source"] = "measured"
    assert MeasurementBundle.model_validate(payload).evidence_source == "measured"

    payload["evidence_source"] = "fixture"
    with pytest.raises(ValidationError):
        MeasurementBundle.model_validate(payload)


def test_pilot_evidence_source_is_simulated_or_measured() -> None:
    payload = {
        "schema_version": "0.1",
        "site_id": "anvil",
        "scheduler": "slurm",
        "evidence_source": "simulated",
        "pilot_id": "pilot-test",
    }
    payload["evidence_source"] = "measured"
    assert PilotResultBundle.model_validate(payload).evidence_source == "measured"

    payload["evidence_source"] = "live"
    with pytest.raises(ValidationError):
        PilotResultBundle.model_validate(payload)


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
