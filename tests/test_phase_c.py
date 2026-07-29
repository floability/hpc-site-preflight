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


def test_anvil_ideal_reference_matches_current_profile_contract() -> None:
    profile = SiteProfile.model_validate(
        _load(ROOT / "examples" / "reference" / "anvil" / "site-profile-ideal.json")
    )
    assert profile.schema_version == "0.4"
    assert profile.evidence_id == "report-anvil-ideal"


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
            "/slurm/partitions/shared/maximum_walltime_seconds",
            "documented_limit_over_visible_configuration",
            "documentation",
        ),
        ("/network/login_compute/tcp_connect", "compute_network_behavior", "pilot"),
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
    rule = get_rule("/slurm/partitions/shared/maximum_walltime_seconds")
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
    scheduler_profile = profile.slurm or profile.htcondor
    assert scheduler_profile is not None
    assert scheduler_profile.submit_command == submit_command
    assert profile.unresolved
    assert profile.conflicts == []
    assert report.site_id == profile.site_id
    assert len(report.evidence) > 0
    assert _object_depth(profile_payload) <= 3
    assert _object_depth(report_payload) <= 2

    assert profile.evidence_id == report.report_id
    evidence_ids = {item.evidence_id for item in report.evidence}
    assert all(set(link.evidence_ids) <= evidence_ids for link in report.links)

    with pytest.raises(ValidationError):
        SiteProfile.model_validate({**profile_payload, "unexpected": True})


def test_anvil_unlimited_walltime_is_preserved_as_measurement() -> None:
    profile, _ = _compile("anvil")
    assert profile.slurm is not None
    shared = next(item for item in profile.slurm.partitions if item.name == "shared")
    assert shared.maximum_walltime_seconds == -1
    assert shared.cpus_per_node == 128
    assert shared.memory_mib_per_node == 257400
    assert "resource_shapes" not in profile.slurm.model_dump()

    gpu = next(item for item in profile.slurm.partitions if item.name == "gpu")
    assert gpu.gpu_count_per_node == 4


def test_anvil_measurements_build_storage_patterns_and_login_identity() -> None:
    profile, report = _compile("anvil")
    storage = {item.id: item for item in profile.storage}

    assert list(storage) == ["home", "project", "scratch"]
    assert storage["home"].path_pattern == "/home/{username}"
    assert storage["project"].path_pattern == "/anvil/projects/{group}"
    assert storage["scratch"].path_pattern == "/anvil/scratch/{username}"
    assert storage["scratch"].login_readable is True
    assert storage["scratch"].login_writable is True
    assert storage["scratch"].compute_visible is None
    assert profile.network.login.hostname_patterns == ["*.anvil.rcac.purdue.edu"]
    assert profile.network.login.outbound_https is None
    assert profile.network.compute.outbound_https is None
    assert profile.network.login_compute.tcp_connect is None
    assert profile.network.compute_compute.verified_ports is None
    assert profile.network.compute_compute.suggested_port_range is None

    evidence_paths = {item.field_path for item in report.evidence}
    assert "/facts/storage/filesystems/scratch/path" in evidence_paths
    assert "/facts/networking/local_tcp_bind" not in evidence_paths
    links = {item.profile_field: item.evidence_ids for item in report.links}
    assert len(links["/storage/scratch/path_pattern"]) == 2
    assert len(links["/storage/project/path_pattern"]) == 2
    assert "/slurm/partitions/shared/cpus_per_node" in links


def test_stampede_measured_walltime_is_preserved() -> None:
    profile, _ = _compile("stampede3")
    assert profile.slurm is not None
    spr = next(item for item in profile.slurm.partitions if item.name == "spr")
    assert spr.maximum_walltime_seconds == 172800


def test_htcondor_profile_has_resource_groups_not_partitions() -> None:
    profile, _ = _compile("notre-dame-crc")
    assert profile.slurm is None
    assert profile.htcondor is not None
    assert profile.htcondor.pool_totals.machine_count == 390
    assert profile.htcondor.pool_totals.cpu_cores == 14242
    assert profile.htcondor.pool_totals.advertised_gpus == 310
    assert len(profile.htcondor.cpu_groups) == 11
    assert len(profile.htcondor.gpu_groups) == 12
    assert [item.name for item in profile.htcondor.submit_attributes] == [
        "universe",
        "executable",
        "request_cpus",
        "request_memory",
        "request_gpus",
        "should_transfer_files",
        "when_to_transfer_output",
    ]
    assert not any("/slurm/partitions/" in item.field for item in profile.unresolved)


def test_measurement_build_is_deterministic() -> None:
    first = _compile("anvil")
    second = _compile("anvil")
    assert first == second
