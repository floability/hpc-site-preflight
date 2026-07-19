"""CLI smoke tests for Milestone 1."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.cli import build_parser, main


def test_parser_accepts_profile_build() -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "profile",
            "build",
            "--mode",
            "simulate",
            "--site-info",
            "examples/simulate/anvil/site-info.json",
        ]
    )
    assert args.command_name == "profile build"
    assert args.mode == "simulate"


@pytest.mark.parametrize("retired_mode", ["fixture", "mock", "replay"])
def test_parser_rejects_retired_mode_names(retired_mode: str) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "profile",
                "build",
                "--mode",
                retired_mode,
                "--site-info",
                "examples/simulate/anvil/site-info.json",
            ]
        )


@pytest.mark.parametrize(
    ("argv", "command_name"),
    [
        (["profile", "validate", "--profile", "site.json"], "profile validate"),
        (["profile", "show", "--site-id", "purdue-anvil"], "profile show"),
        (["evidence", "capture-login", "--output", "measurements.json"], "evidence capture-login"),
        (
            [
                "evidence",
                "run-pilots",
                "--site-info",
                "site.json",
                "--output",
                "pilots.json",
                "--scheduler",
                "slurm",
            ],
            "evidence run-pilots",
        ),
        (
            ["evaluate", "documentation", "--site-info", "site.json"],
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
        (["profile", "build", "--help"], "--site-info SITE_INFO"),
        (
            ["evaluate", "documentation", "--help"],
            "{full-corpus,bm25,schema-expanded-bm25}",
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


def test_simulated_profile_build_writes_phase_c_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    run_dir = tmp_path / "runs"
    exit_code = main(
        [
            "profile",
            "build",
            "--site-info",
            "examples/simulate/anvil/site-info.json",
            "--measurements",
            "examples/simulate/anvil/login-measurements.json",
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
    assert profile["site_id"] == evidence["site_id"] == "purdue-anvil"
    assert profile["profile_state"] == "partial"

    report_path = next(run_dir.glob("*/performance.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert [stage["name"] for stage in report["steps"]] == [
        "site_info_load",
        "simulated_measurement_load",
        "simulated_measurement_validate",
        "measurement_profile_build",
        "profile_artifact_write",
    ]
    assert {artifact["kind"] for artifact in report["artifacts"]} >= {
        "site_profile",
        "evidence_report",
    }
