"""Minimal Phase C profile, evidence, rule, and compiler tests."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import EvidenceRecord
from hpc_site_preflight.evidence.reconciliation import (
    get_rule,
    is_not_applicable,
    select_preferred_source,
)
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.profiles.models import SiteProfile, SubmissionOption

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _compile(simulation_name: str) -> tuple[SiteProfile, EvidenceReport]:
    root = SIMULATE_ROOT / simulation_name
    measurements = MeasurementBundle.model_validate(_load(root / "login-measurements.json"))
    return compile_profile(measurements)


def _object_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_object_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return max((_object_depth(item) for item in value), default=0)
    return 0


def test_checked_site_profile_schema_matches_model_envelope() -> None:
    checked = _load(ROOT / "schemas" / "site-profile.schema.json")
    generated = SiteProfile.model_json_schema()

    assert checked["additionalProperties"] is False
    assert set(checked["properties"]) == set(generated["properties"])
    assert set(checked["required"]) == set(generated["required"])


def test_submission_option_preserves_syntax_order() -> None:
    option = SubmissionOption(
        name="account",
        syntax=["-A {account}", "--account={account}"],
        required=True,
    )
    assert option.syntax == ["-A {account}", "--account={account}"]
    assert option.required is True


def test_detailed_evidence_supports_documentation_provenance() -> None:
    record = EvidenceRecord(
        evidence_id="documentation:example",
        field_path="/accounting/charging_model",
        source_type="documentation",
        scope="target_site",
        trust="official",
        disposition="accepted",
        value="node_hour",
        observed_at="2026-07-19T12:00:00Z",
        freshness="site_change",
        documentation_url="https://example.edu/policy",
        documentation_heading="Charging",
        exact_quote="Jobs are charged by node-hour.",
        chunk_id="chunk-1",
    )
    assert record.documentation_heading == "Charging"


@pytest.mark.parametrize(
    ("field", "expected_rule", "preferred"),
    [
        (
            "/partitions/shared/maximum_walltime_seconds",
            "documented_limit_over_visible_configuration",
            "documentation",
        ),
        ("/network/login_compute/tcp_connect", "compute_network_behavior", "pilot"),
        ("/accounting/visible_accounts", "visible_accounts", "measurement"),
        ("/accounting/allocation_required", "allocation_requirement", "documentation"),
    ],
)
def test_rule_table_selects_expected_source(
    field: str, expected_rule: str, preferred: str
) -> None:
    rule = get_rule(field)
    assert rule is not None
    assert rule.rule_id == expected_rule
    assert select_preferred_source(
        rule, {"measurement", "documentation", "pilot"}
    ) == preferred


def test_slurm_partition_rules_are_not_applicable_to_htcondor() -> None:
    rule = get_rule("/partitions/shared/maximum_walltime_seconds")
    assert rule is not None
    assert is_not_applicable(rule, "htcondor") is True
    assert is_not_applicable(rule, "slurm") is False


@pytest.mark.parametrize(
    ("simulation_name", "scheduler", "submit_command"),
    [
        ("anvil", "slurm", "sbatch"),
        ("stampede3", "slurm", "sbatch"),
        ("notre-dame-crc", "htcondor", "condor_submit"),
    ],
)
def test_measurement_only_builder_supports_all_sites(
    simulation_name: str, scheduler: str, submit_command: str
) -> None:
    profile, report = _compile(simulation_name)
    profile_payload = profile.model_dump(mode="json")
    report_payload = report.model_dump(mode="json")

    assert profile.profile_state == "partial"
    assert profile.scheduler_type == scheduler
    assert profile.submit_command == submit_command
    assert profile.unresolved
    assert profile.conflicts == []
    assert report.site_id == profile.site_id
    assert len(report.evidence) > 0
    assert _object_depth(profile_payload) <= 3
    assert _object_depth(report_payload) <= 2

    evidence_ids = {item.evidence_id for item in report.evidence}
    assert all(set(link.evidence_ids) <= evidence_ids for link in profile.field_evidence)

    with pytest.raises(ValidationError):
        SiteProfile.model_validate({**profile_payload, "unexpected": True})


def test_anvil_missing_visible_walltime_is_not_promoted_to_policy() -> None:
    profile, _ = _compile("anvil")
    shared = next(item for item in profile.partitions if item.name == "shared")
    assert shared.visible_walltime_seconds is None
    assert shared.maximum_walltime_seconds is None


def test_anvil_measurements_build_storage_patterns_and_login_identity() -> None:
    profile, report = _compile("anvil")
    storage = {item.name: item for item in profile.storage}

    assert list(storage) == ["home", "project", "data", "scratch", "tmp"]
    assert storage["home"].path_pattern == "/home/{username}"
    assert storage["project"].path_pattern == "/anvil/projects/{group}"
    assert storage["data"].path_pattern is None
    assert storage["scratch"].path_pattern == "/anvil/scratch/{username}"
    assert storage["scratch"].login_readable is True
    assert storage["scratch"].login_writable is True
    assert storage["scratch"].compute_visible is None
    assert profile.network.login.hostname_patterns == ["*.anvil.rcac.purdue.edu"]
    assert storage["tmp"].path_pattern == "/tmp"
    assert profile.network.login.outbound_https is None
    assert profile.network.compute.outbound_https is None
    assert profile.network.login_compute.tcp_connect is None
    assert profile.network.compute_compute.verified_tcp_port_range is None

    evidence_paths = {item.field_path for item in report.evidence}
    assert "/facts/storage/filesystems/scratch/path" in evidence_paths
    assert "/facts/networking/local_tcp_bind" not in evidence_paths
    links = {item.field: item.evidence_ids for item in profile.field_evidence}
    assert len(links["/storage/scratch/path_pattern"]) == 2
    assert len(links["/storage/project/path_pattern"]) == 2


def test_stampede_visible_duration_is_normalized() -> None:
    profile, _ = _compile("stampede3")
    spr = next(item for item in profile.partitions if item.name == "spr")
    assert spr.visible_walltime_seconds == 172800
    assert spr.maximum_walltime_seconds is None


def test_htcondor_profile_has_resource_groups_not_partitions() -> None:
    profile, _ = _compile("notre-dame-crc")
    assert profile.partitions == []
    assert {item.key for item in profile.resource_groups}
    assert not any("/partitions/" in item.field for item in profile.unresolved)


def test_measurement_build_is_deterministic() -> None:
    first = _compile("anvil")
    second = _compile("anvil")
    assert first == second
