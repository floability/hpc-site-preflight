"""RunTracker aggregation and failure tests."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.reporting.tracker import RunTracker


def test_tracker_aggregates_stage_usage(tmp_path: Path) -> None:
    tracker = RunTracker(
        command="test", mode="simulate", run_root=tmp_path, quiet=True, run_id="run"
    )
    initial_report = json.loads(tracker.performance_path.read_text(encoding="utf-8"))
    assert initial_report["status"] == "running"
    assert initial_report["steps"] == []

    with tracker.stage("documentation_discovery"):
        tracker.record_model_usage(input_tokens=100, output_tokens=25, requests=2)
        tracker.record_retry()
        tracker.record_tool_call(3)

    stage_report = json.loads(tracker.performance_path.read_text(encoding="utf-8"))
    assert stage_report["status"] == "running"
    assert stage_report["steps"][0]["status"] == "completed"
    path = tracker.finalize(status="completed")

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["model_usage"]["requests"] == 2
    assert report["model_usage"]["total_tokens"] == 125
    assert report["retries"] == 1
    assert report["tool_calls"] == 3
    assert report["steps"][0]["name"] == "documentation_discovery"
    assert {artifact["kind"] for artifact in report["artifacts"]} == {
        "execution_trace",
        "performance_report",
    }


def test_tracker_records_failed_stage(tmp_path: Path) -> None:
    tracker = RunTracker(command="test", run_root=tmp_path, quiet=True, run_id="failed")
    try:
        with tracker.stage("policy_extraction"):
            tracker.record_model_usage(input_tokens=50, output_tokens=0)
            raise ValueError("bad extraction")
    except ValueError as exc:
        tracker.record_run_error(exc)
    path = tracker.finalize(status="failed")

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["steps"][0]["status"] == "failed"
    assert report["steps"][0]["error_type"] == "ValueError"
    assert report["model_usage"]["total_tokens"] == 50


def test_tracker_marks_missing_provider_usage_unavailable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    tracker = RunTracker(command="test", run_root=tmp_path, quiet=False, run_id="usage")
    with tracker.stage("policy_extraction"):
        tracker.record_model_usage(requests=1, usage_available=False)
    path = tracker.finalize(status="completed")

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["steps"][0]["model_usage"]["usage_available"] is False
    assert report["model_usage"]["usage_available"] is False
    captured = capsys.readouterr()
    assert "tokens=unavailable" in captured.err
    output = captured.out
    assert "Total tokens:  unavailable" in output


def test_tracker_rejects_second_finalization(tmp_path: Path) -> None:
    tracker = RunTracker(command="test", run_root=tmp_path, quiet=True, run_id="once")
    tracker.finalize(status="completed")

    with pytest.raises(RuntimeError, match="only be called once"):
        tracker.finalize(status="completed")
