"""CLI smoke tests for Milestone 1."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.cli import build_parser, main
from hpc_site_preflight.measurements.base import MeasurementBundle


def test_parser_accepts_profile_build() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "profile",
            "build",
            "--measurements",
            "examples/simulate/anvil/login-measurements.json",
        ]
    )
    assert args.command_name == "profile build"
    assert args.site_mode == "simulate"
    assert args.model_mode == "live"
    assert args.web_mode == "live"


def test_parser_accepts_documentation_discovery_hints() -> None:
    args = build_parser().parse_args(
        [
            "profile",
            "build",
            "--site-name",
            "Purdue Anvil",
            "--discovery-note",
            "Use the RCAC user guide.",
            "--discovery-keyword",
            "RCAC",
            "--discovery-keyword",
            "queues",
        ]
    )

    assert args.site_name == "Purdue Anvil"
    assert args.discovery_note == "Use the RCAC user guide."
    assert args.discovery_keyword == ["RCAC", "queues"]


@pytest.mark.parametrize("option", ["--mode", "--provider"])
def test_parser_rejects_retired_mode_options(option: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "profile",
                "build",
                option,
                "simulate",
                "--measurements",
                "examples/simulate/anvil/login-measurements.json",
            ]
        )


@pytest.mark.parametrize(
    ("argv", "command_name"),
    [
        (["profile", "validate", "--profile", "site.json"], "profile validate"),
        (["profile", "show", "--site-id", "purdue-anvil"], "profile show"),
        (
            [
                "evidence",
                "capture-login",
                "--short-site-name",
                "Example",
                "--output",
                "measurements.json",
            ],
            "evidence capture-login",
        ),
        (
            [
                "evidence",
                "run-pilots",
                "--measurements",
                "measurements.json",
                "--output",
                "pilots.json",
                "--scheduler",
                "slurm",
            ],
            "evidence run-pilots",
        ),
        (
            [
                "evaluate",
                "documentation",
                "--measurements",
                "measurements.json",
            ],
            "evaluate documentation",
        ),
        (
            ["preflight", "--backpack", "backpack", "--site-profile", "site.json"],
            "preflight",
        ),
    ],
)
def test_parser_accepts_each_command(argv: list[str], command_name: str) -> None:
    args = build_parser().parse_args(argv)
    assert args.command_name == command_name


@pytest.mark.parametrize(
    ("argv", "expected_text"),
    [
        (["--help"], "{profile,evidence,evaluate,preflight}"),
        (["profile", "build", "--help"], "--measurements MEASUREMENTS"),
        (
            ["evaluate", "documentation", "--help"],
            "{full-corpus,bm25,llm-expanded-bm25}",
        ),
    ],
)
def test_acceptance_help_commands(
    argv: list[str],
    expected_text: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(argv)

    assert raised.value.code == 0
    assert expected_text in capsys.readouterr().out


def test_unimplemented_command_writes_failed_report(tmp_path: Path) -> None:
    exit_code = main(
        [
            "profile",
            "validate",
            "--profile",
            "site-profile.json",
            "--run-dir",
            str(tmp_path),
            "--quiet",
        ]
    )
    assert exit_code == 2
    reports = list(tmp_path.glob("*/performance.json"))
    traces = list(tmp_path.glob("*/trace.jsonl"))
    assert len(reports) == 1
    assert len(traces) == 1

    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["command"] == "profile validate"
    assert report["status"] == "failed"
    assert report["error_type"] == "FeatureNotImplementedError"
    assert report["steps"][0]["name"] == "command_dispatch"
    assert report["steps"][0]["status"] == "failed"
    assert {artifact["kind"] for artifact in report["artifacts"]} == {
        "execution_trace",
        "performance_report",
    }

    trace_events = [
        json.loads(line)["event"] for line in traces[0].read_text(encoding="utf-8").splitlines()
    ]
    assert trace_events == [
        "run_started",
        "stage_started",
        "stage_finished",
        "run_error",
        "run_finished",
    ]


def test_simulated_profile_build_writes_phase_d_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    run_dir = tmp_path / "runs"
    exit_code = main(
        [
            "profile",
            "build",
            "--measurements",
            "examples/simulate/anvil/login-measurements.json",
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--output-dir",
            str(output_dir),
            "--run-dir",
            str(run_dir),
            "--quiet",
        ]
    )

    assert exit_code == 0
    profile = json.loads((output_dir / "site-profile.json").read_text(encoding="utf-8"))
    evidence = json.loads((output_dir / "evidence-report.json").read_text(encoding="utf-8"))
    assert profile["site_id"] == evidence["site_id"] == "anvil"
    assert profile["profile_state"] == "partial"
    assert list(profile)[:4] == ["schema_version", "site_id", "site_name", "aliases"]
    assert list(profile)[-2:] == ["evidence_report", "field_evidence"]
    documentation = json.loads(
        (output_dir / "documentation-evidence.json").read_text(encoding="utf-8")
    )
    assert documentation["model_mode"] == "simulate"
    assert documentation["model_provider"] == "recorded"
    assert documentation["model"] is None
    assert documentation["web_mode"] == "simulate"

    report_path = next(run_dir.glob("*/performance.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["mode"] == "site=simulate, model=simulate, web=simulate"
    assert report["model_usage"]["usage_available"] is False
    stage_names = [stage["name"] for stage in report["steps"]]
    assert stage_names[:3] == [
        "simulated_measurement_load",
        "simulated_measurement_validate",
        "measurement_profile_build",
    ]
    assert "documentation_corpus" in stage_names
    assert "documentation_context_selection" in stage_names
    assert "documentation_evidence_validation" in stage_names
    assert stage_names[-2:] == ["documentation_profile_apply", "profile_artifact_write"]
    assert {artifact["kind"] for artifact in report["artifacts"]} >= {
        "site_profile",
        "evidence_report",
        "documentation_evidence",
        "documentation_corpus",
    }
    trace_path = next(run_dir.glob("*/trace.jsonl"))
    trace = trace_path.read_text(encoding="utf-8")
    assert "Anvil jobs are submitted" not in trace
    assert "content_hash" in trace


def test_profile_build_prints_concise_discovery_progress(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "profile",
            "build",
            "--measurements",
            "examples/simulate/anvil/login-measurements.json",
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--output-dir",
            str(tmp_path / "output"),
            "--run-dir",
            str(tmp_path / "runs"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Searching official documentation with 10 queries" in captured.err
    assert "Search 1/10: canonical" in captured.err
    assert "Ranked 2 unique documentation candidate(s)" in captured.err
    assert "Fetch 1/10:" in captured.err
    assert "Asking the model to select from 2 fetched" in captured.err
    assert "Discovery selected 2 target-site page(s)" in captured.err
    assert "  Time:" not in captured.err


def test_profile_build_keeps_partial_output_when_documentation_is_missing(
    tmp_path: Path,
) -> None:
    source = Path("examples/simulate/anvil")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    measurement_path = inputs / "login-measurements.json"
    measurement_path.write_text(
        (source / "login-measurements.json").read_text(encoding="utf-8")
    )
    output = tmp_path / "output"

    exit_code = main(
        [
            "profile",
            "build",
            "--measurements",
            str(measurement_path),
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--output-dir",
            str(output),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    documentation = json.loads(
        (output / "documentation-evidence.json").read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert documentation["findings"] == []
    assert documentation["rejected"]
    assert (output / "site-profile.json").exists()


def test_simulated_profile_build_requires_measurements(tmp_path: Path) -> None:
    exit_code = main(
        [
            "profile",
            "build",
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 2
    report_path = next((tmp_path / "runs").glob("*/performance.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["error_type"] == "ConfigurationError"
    assert "requires --measurements" in report["error_message"]


def test_capture_login_writes_structured_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = MeasurementBundle.model_validate(
        json.loads(
            Path("examples/simulate/anvil/login-measurements.json").read_text(
                encoding="utf-8"
            )
        )
    )
    monkeypatch.setattr(
        "hpc_site_preflight.operations.LiveMeasurementProvider.collect",
        lambda self, tracker: bundle,
    )
    output = tmp_path / "login-measurements.json"

    exit_code = main(
        [
            "evidence",
            "capture-login",
            "--short-site-name",
            "Anvil",
            "--output",
            str(output),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["schema_version"] == "0.6"
    assert result["site_facts"]["site_name"] == "Anvil"
    assert isinstance(result["slurm"]["partitions"], list)


def test_live_profile_build_collects_when_measurements_are_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = MeasurementBundle.model_validate(
        json.loads(
            Path("examples/simulate/anvil/login-measurements.json").read_text(
                encoding="utf-8"
            )
        )
    )
    monkeypatch.setattr(
        "hpc_site_preflight.operations.LiveMeasurementProvider.collect",
        lambda self, tracker: bundle,
    )
    output = tmp_path / "output"

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
            "--output-dir",
            str(output),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    assert (output / "login-measurements.json").exists()
    assert (output / "site-profile.json").exists()


def test_live_profile_build_reuses_supplied_measurements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(self, tracker):
        raise AssertionError("live collection should not run")

    monkeypatch.setattr(
        "hpc_site_preflight.operations.LiveMeasurementProvider.collect",
        fail_if_called,
    )

    exit_code = main(
        [
            "profile",
            "build",
            "--site-mode",
            "live",
            "--measurements",
            "examples/simulate/anvil/login-measurements.json",
            "--model-mode",
            "simulate",
            "--web-mode",
            "simulate",
            "--output-dir",
            str(tmp_path / "output"),
            "--run-dir",
            str(tmp_path / "runs"),
            "--quiet",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "output" / "site-profile.json").exists()
    assert not (tmp_path / "output" / "login-measurements.json").exists()
