"""Pilot ingestion and deterministic profile reconciliation tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hpc_site_preflight.cli import main
from hpc_site_preflight.evidence.models import EvidenceLink, EvidenceRecord
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.probes.base import PilotResultBundle
from hpc_site_preflight.probes.slurm import SlurmPilot
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.profiles.models import FieldEvidenceLink
from hpc_site_preflight.profiles.pilots import (
    apply_pilot_results,
    suggested_port_range,
)

ROOT = Path(__file__).resolve().parents[1]
ANVIL = ROOT / "examples" / "simulate" / "anvil"


def pilot_payload() -> dict:
    """Return a small standalone-style Slurm pilot result."""

    return {
        "schema_version": "0.1",
        "run_id": "pilot-test",
        "site_id": "anvil",
        "scheduler": "slurm",
        "evidence_source": "simulated",
        "collected_at": "2026-07-27T12:00:00Z",
        "pilot_id": "pilot-test",
        "status": "completed",
        "storage_results": [
            {
                "name": "home",
                "path": "/home/example",
                "visible": True,
                "readable": True,
                "writable": True,
            },
            {
                "name": "scratch",
                "path": "/anvil/scratch/example",
                "visible": True,
                "readable": True,
                "writable": True,
            },
        ],
        "login_compute_attempts": [
            {"port": 9200, "status": "passed", "error": None},
            {"port": 10100, "status": "passed", "error": None},
        ],
        "compute_compute_attempts": [
            {"port": 30000, "status": "passed", "error": None},
            {"port": 33720, "status": "passed", "error": None},
        ],
        "primary_node": "a001",
        "peer_node": "a002",
        "distinct_nodes": True,
        "errors": [],
    }


def pilot_bundle() -> PilotResultBundle:
    """Wrap the standalone result in the normalized provider contract."""

    payload = {**pilot_payload(), "evidence_source": "simulated"}
    return PilotResultBundle.model_validate(payload)


def test_suggested_range_covers_successful_thousand_blocks() -> None:
    assert suggested_port_range([9200, 10100]) == "9000-10999"
    assert suggested_port_range([30000, 33720]) == "30000-33999"
    assert suggested_port_range([]) is None


def test_completed_failures_set_false_without_a_suggested_range() -> None:
    measurements = MeasurementBundle.model_validate_json(
        (ANVIL / "login-measurements.json").read_text(encoding="utf-8")
    )
    profile, report = compile_profile(measurements)
    payload = {
        **pilot_payload(),
        "login_compute_attempts": [
            {"port": 9000, "status": "failed", "error": "timeout"}
        ],
        "compute_compute_attempts": None,
    }

    profile, _ = apply_pilot_results(
        profile,
        report,
        PilotResultBundle.model_validate(payload),
    )

    assert profile.network.login_compute.tcp_connect is False
    assert profile.network.login_compute.verified_ports == []
    assert profile.network.login_compute.suggested_port_range is None
    assert profile.network.compute_compute.tcp_connect is None


def test_pilot_results_fill_storage_network_and_evidence() -> None:
    measurements = MeasurementBundle.model_validate_json(
        (ANVIL / "login-measurements.json").read_text(encoding="utf-8")
    )
    profile, report = compile_profile(measurements)

    profile, report = apply_pilot_results(profile, report, pilot_bundle())

    storage = {item.name: item for item in profile.storage}
    assert storage["home"].compute_visible is True
    assert storage["scratch"].compute_readable is True
    assert profile.network.login_compute.verified_ports == [9200, 10100]
    assert profile.network.login_compute.suggested_port_range == "9000-10999"
    assert profile.network.compute_compute.suggested_port_range == "30000-33999"
    assert profile.network.compute_compute.tcp_connect is True
    assert not any(
        item.field == "/network/compute_compute/tcp_connect"
        for item in profile.unresolved
    )
    pilot_evidence = [item for item in report.evidence if item.source_type == "pilot"]
    assert pilot_evidence
    assert all(item.pilot_id == "pilot-test" for item in pilot_evidence)


def test_pilot_disagreement_remains_visible() -> None:
    measurements = MeasurementBundle.model_validate_json(
        (ANVIL / "login-measurements.json").read_text(encoding="utf-8")
    )
    profile, report = compile_profile(measurements)
    profile.network.login_compute.tcp_connect = False
    profile.field_evidence.append(
        FieldEvidenceLink(
            field="/network/login_compute/tcp_connect",
            evidence_ids=["documentation-network"],
        )
    )
    report.evidence.append(
        EvidenceRecord(
            evidence_id="documentation-network",
            field_path="/network/login_compute/tcp_connect",
            source_type="documentation",
            scope="target_site",
            trust="official",
            disposition="accepted",
            value=False,
            freshness="site_change",
        )
    )
    report.links.append(
        EvidenceLink(
            profile_field="/network/login_compute/tcp_connect",
            evidence_ids=["documentation-network"],
        )
    )

    profile, report = apply_pilot_results(profile, report, pilot_bundle())

    assert profile.network.login_compute.tcp_connect is None
    assert profile.conflicts[0].other_evidence == ["documentation-network"]
    assert report.conflicts[0].selected_evidence_id.startswith("pilot:")
    assert any(
        item.field == "/network/login_compute/tcp_connect"
        and item.next_action == "admin_confirmation"
        for item in profile.unresolved
    )


def test_profile_build_accepts_recorded_pilot_result(tmp_path: Path) -> None:
    pilot_path = tmp_path / "pilot-result.json"
    pilot_path.write_text(json.dumps(pilot_payload()), encoding="utf-8")
    output_dir = tmp_path / "artifacts"

    exit_code = main(
        [
            "profile",
            "build",
            "--measurements",
            str(ANVIL / "login-measurements.json"),
            "--pilot-results",
            str(pilot_path),
            "--run-pilots",
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--output-dir",
            str(output_dir),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    profile = json.loads((output_dir / "site-profile.json").read_text())
    assert profile["network"]["login_compute"]["verified_ports"] == [9200, 10100]
    assert profile["network"]["compute_compute"]["suggested_port_range"] == "30000-33999"
    assert (output_dir / "pilot-evidence.json").exists()


def test_failed_recorded_pilot_still_writes_partial_profile(tmp_path: Path) -> None:
    pilot_path = tmp_path / "pilot-result.json"
    pilot_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "site_id": "anvil",
                "scheduler": "slurm",
                "evidence_source": "simulated",
                "pilot_id": "failed-pilot",
                "status": "failed",
                "errors": ["The primary job was held."],
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "artifacts"

    exit_code = main(
        [
            "profile",
            "build",
            "--measurements",
            str(ANVIL / "login-measurements.json"),
            "--run-pilots",
            "--pilot-results",
            str(pilot_path),
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--output-dir",
            str(output_dir),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    profile = json.loads((output_dir / "site-profile.json").read_text())
    assert profile["profile_state"] == "partial"
    assert profile["network"]["login_compute"]["tcp_connect"] is None
    evidence = json.loads((output_dir / "evidence-report.json").read_text())
    assert any(
        item["source_type"] == "pilot"
        and item["disposition"] == "invalid"
        and item["reason"] == "The primary job was held."
        for item in evidence["evidence"]
    )


def test_live_profile_collects_measurements_then_runs_slurm_pilot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    measured = MeasurementBundle.model_validate_json(
        (ANVIL / "login-measurements.json").read_text(encoding="utf-8")
    )
    events: list[str] = []
    captured_inputs = []

    def collect_live(_provider: object, _tracker: object) -> MeasurementBundle:
        events.append("login_measurement")
        return measured

    def run_slurm(_runner: SlurmPilot, inputs: object) -> PilotResultBundle:
        events.append("slurm_pilot")
        captured_inputs.append(inputs)
        return PilotResultBundle.model_validate(
            {
                **pilot_payload(),
                "evidence_source": "measured",
            }
        )

    monkeypatch.setattr(
        "hpc_site_preflight.operations.LiveMeasurementProvider.collect",
        collect_live,
    )
    monkeypatch.setattr(SlurmPilot, "run", run_slurm)
    output_dir = tmp_path / "artifacts"

    exit_code = main(
        [
            "profile",
            "build",
            "--site-mode",
            "live",
            "--short-site-name",
            "Anvil",
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--web-recording",
            str(ANVIL / "documentation-web.json"),
            "--model-recording",
            str(ANVIL / "documentation-model.json"),
            "--run-pilots",
            "--pilot-start-port",
            "30000",
            "--output-dir",
            str(output_dir),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    assert events == ["login_measurement", "slurm_pilot"]
    assert (output_dir / "login-measurements.json").exists()
    assert (output_dir / "pilot-evidence.json").exists()
    assert captured_inputs[0].start_port == 30000
    assert captured_inputs[0].login_host == measured.site_facts.fqdn
    assert set(captured_inputs[0].storage) == {"home", "scratch", "project"}
    profile = json.loads((output_dir / "site-profile.json").read_text())
    assert profile["network"]["login_compute"]["verified_ports"] == [9200, 10100]
