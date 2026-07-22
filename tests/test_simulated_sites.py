"""Checks for the three laptop-development site simulations."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.site_descriptor.models import SiteDescriptor

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"
SITE_IDS = {
    "anvil": ("purdue-anvil", "slurm"),
    "stampede3": ("tacc-stampede3", "slurm"),
    "notre-dame-crc": ("notre-dame-crc", "htcondor"),
}
SITE_DESCRIPTOR_KEYS = {
    "schema_version",
    "site_id",
    "site_name",
    "scheduler",
    "aliases",
    "hostname_patterns",
    "documentation",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _object_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_object_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return max((_object_depth(item) for item in value), default=0)
    return 0


def test_site_descriptor_schema_describes_the_shared_shape() -> None:
    schema = _load(ROOT / "schemas" / "site-descriptor.schema.json")

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == SITE_DESCRIPTOR_KEYS
    assert set(schema["properties"]) == SITE_DESCRIPTOR_KEYS
    assert schema["properties"]["scheduler"]["enum"] == ["slurm", "htcondor", "unknown"]


@pytest.mark.parametrize("simulation_name", SITE_IDS)
def test_site_simulation_pair_validates(simulation_name: str) -> None:
    expected_site_id, expected_scheduler = SITE_IDS[simulation_name]
    site_payload = _load(SIMULATE_ROOT / simulation_name / "site-descriptor.json")
    measurement_payload = _load(SIMULATE_ROOT / simulation_name / "login-measurements.json")
    site = SiteDescriptor.model_validate(site_payload)
    measurements = MeasurementBundle.model_validate(measurement_payload)

    assert set(site_payload) == SITE_DESCRIPTOR_KEYS
    assert _object_depth(site_payload) <= 2
    assert _object_depth(measurement_payload) <= 2
    assert site.site_id == measurements.site_id == expected_site_id
    assert site.scheduler == measurements.scheduler_type == expected_scheduler
    assert measurements.evidence_source == "simulated"

    observations = [*measurements.common, *measurements.scheduler]
    assert observations
    assert len({item.path for item in observations}) == len(observations)
    assert all(item.source_reference for item in observations)


def test_anvil_simulation_preserves_visible_walltime_conflict_input() -> None:
    bundle = MeasurementBundle.model_validate(
        _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    )
    observations = {item.path: item for item in bundle.scheduler}
    walltime = observations["/facts/scheduler/partitions/shared/visible_walltime_limit"]

    assert walltime.value == "infinite"
    assert walltime.source_reference is not None
    assert "not enforced policy" in walltime.source_reference


def test_stampede3_simulation_uses_the_slurm_structure() -> None:
    bundle = MeasurementBundle.model_validate(
        _load(SIMULATE_ROOT / "stampede3" / "login-measurements.json")
    )
    paths = {item.path for item in bundle.scheduler}

    assert "/facts/scheduler/partitions" in paths
    assert "/facts/scheduler/node_shapes" in paths


def test_notre_dame_simulation_uses_classads_and_resource_groups() -> None:
    bundle = MeasurementBundle.model_validate(
        _load(SIMULATE_ROOT / "notre-dame-crc" / "login-measurements.json")
    )
    paths = {item.path for item in bundle.scheduler}

    assert not any("/partitions" in path for path in paths)
    assert "/facts/scheduler/raw_slot_classads" in paths
    assert "/facts/scheduler/resource_groups" in paths
    assert any(path.endswith("/slot_kind") for path in paths)


def test_scheduler_models_reject_cross_scheduler_fields() -> None:
    slurm_payload = _load(SIMULATE_ROOT / "anvil" / "login-measurements.json")
    slurm_payload["scheduler_type"] = "htcondor"
    with pytest.raises(ValidationError, match="not HTCondor measurements"):
        MeasurementBundle.model_validate(slurm_payload)

    htcondor_payload = _load(SIMULATE_ROOT / "notre-dame-crc" / "login-measurements.json")
    htcondor_payload["scheduler_type"] = "slurm"
    with pytest.raises(ValidationError, match="not Slurm measurements"):
        MeasurementBundle.model_validate(htcondor_payload)
