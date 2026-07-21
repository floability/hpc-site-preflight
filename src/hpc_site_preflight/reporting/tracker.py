"""Shared run tracker for time, model usage, retries, tools, artifacts, and failures."""

from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from hpc_site_preflight.reporting.console import print_run_summary, print_stage_summary
from hpc_site_preflight.reporting.models import (
    ArtifactRecord,
    ModelUsage,
    RunPerformance,
    RunStatus,
    StageMetrics,
)
from hpc_site_preflight.reporting.trace import JsonlTrace


class RunTracker:
    """Accumulate measurements for one command and emit reports after every stage."""

    def __init__(
        self,
        *,
        command: str,
        run_root: Path,
        mode: str | None = None,
        quiet: bool = False,
        run_id: str | None = None,
    ) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self.command = command
        self.mode = mode
        self.quiet = quiet
        self.run_path = run_root / self.run_id
        self.run_path.mkdir(parents=True, exist_ok=True)
        self.performance_path = self.run_path / "performance.json"
        self.trace_path = self.run_path / "trace.jsonl"
        self._trace = JsonlTrace(self.trace_path)
        self._started_monotonic = time.perf_counter()
        self._report = RunPerformance(
            run_id=self.run_id,
            command=command,
            mode=mode,
            started_at=datetime.now(UTC),
            artifacts=[
                ArtifactRecord(kind="performance_report", path=self.performance_path),
                ArtifactRecord(kind="execution_trace", path=self.trace_path),
            ],
        )
        self._current_stage: StageMetrics | None = None
        self._finalized = False
        self._trace.write("run_started", run_id=self.run_id, command=command, mode=mode)
        self._write_performance()

    @contextmanager
    def stage(self, name: str, *, display: bool = True) -> Iterator[StageMetrics]:
        """Measure one sequential pipeline stage and report it immediately."""

        if self._current_stage is not None:
            raise RuntimeError("Nested tracker stages are not supported in Milestone 1.")
        stage = StageMetrics(name=name, started_at=datetime.now(UTC))
        self._current_stage = stage
        started = time.perf_counter()
        self._trace.write("stage_started", stage=name)
        if not self.quiet and display:
            print(f"[starting] {name}", file=sys.stderr, flush=True)
        try:
            yield stage
        except Exception as exc:
            stage.status = "failed"
            stage.error_type = type(exc).__name__
            stage.error_message = str(exc)
            raise
        else:
            stage.status = "completed"
        finally:
            stage.ended_at = datetime.now(UTC)
            stage.duration_seconds = time.perf_counter() - started
            self._report.steps.append(stage)
            self._aggregate_stage(stage)
            self._trace.write("stage_finished", **stage.model_dump(mode="json"))
            self._current_stage = None
            self._write_performance()
            if not self.quiet and display:
                print_stage_summary(stage)

    def record_model_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        requests: int = 1,
        usage_available: bool = True,
    ) -> None:
        """Append provider-reported usage to the active stage."""

        self._require_non_negative("input_tokens", input_tokens)
        self._require_non_negative("output_tokens", output_tokens)
        self._require_non_negative("requests", requests)
        stage = self._require_stage("model usage")
        stage.model_usage.requests += requests
        stage.model_usage.input_tokens += input_tokens
        stage.model_usage.output_tokens += output_tokens
        stage.model_usage.total_tokens += input_tokens + output_tokens
        stage.model_usage.usage_available = stage.model_usage.usage_available and usage_available

    def record_retry(self, count: int = 1) -> None:
        """Append retry events to the active stage."""

        self._require_non_negative("retry count", count)
        self._require_stage("retry").retries += count

    def record_tool_call(
        self,
        count: int = 1,
        *,
        tool_name: str | None = None,
        details: dict[str, str] | None = None,
    ) -> None:
        """Append tool-call metrics and optional body-free trace details."""

        self._require_non_negative("tool-call count", count)
        self._require_stage("tool call").tool_calls += count
        if tool_name is not None:
            self._trace.write("tool_call", tool=tool_name, details=details or {})

    def record_tool_result(
        self,
        *,
        tool_name: str,
        details: dict[str, str] | None = None,
    ) -> None:
        """Trace a tool result without increasing the tool-call count."""

        self._require_stage("tool result")
        self._trace.write("tool_result", tool=tool_name, details=details or {})

    def progress(self, message: str) -> None:
        """Write one safe progress message to the trace and live console."""

        self._trace.write("progress", message=message)
        if not self.quiet:
            elapsed = time.perf_counter() - self._started_monotonic
            print(f"[{elapsed:6.1f}s] {message}", file=sys.stderr, flush=True)

    def add_artifact(self, *, kind: str, path: Path) -> None:
        """Record an artifact produced by the run."""

        record = ArtifactRecord(kind=kind, path=path)
        self._report.artifacts.append(record)
        self._trace.write("artifact_created", kind=kind, path=str(path))

    def record_run_error(self, exc: Exception) -> None:
        """Record the top-level error while preserving failed stage metrics."""

        self._report.error_type = type(exc).__name__
        self._report.error_message = str(exc)
        self._trace.write("run_error", error_type=type(exc).__name__, error_message=str(exc))

    def finalize(self, *, status: RunStatus) -> Path:
        """Finalize and persist the report exactly once at command exit."""

        if self._finalized:
            raise RuntimeError("RunTracker.finalize() may only be called once.")
        if self._current_stage is not None:
            raise RuntimeError("Cannot finalize a run while a tracker stage is active.")
        self._report.status = status
        self._report.ended_at = datetime.now(UTC)
        self._report.duration_seconds = time.perf_counter() - self._started_monotonic
        self._trace.write("run_finished", status=status, report=str(self.performance_path))
        self._write_performance()
        self._finalized = True
        if not self.quiet:
            print_run_summary(self._report, str(self.performance_path))
        return self.performance_path

    @property
    def report(self) -> RunPerformance:
        """Return the current mutable aggregate for tests and adapters."""

        return self._report

    def _aggregate_stage(self, stage: StageMetrics) -> None:
        run_usage: ModelUsage = self._report.model_usage
        run_usage.requests += stage.model_usage.requests
        run_usage.input_tokens += stage.model_usage.input_tokens
        run_usage.output_tokens += stage.model_usage.output_tokens
        run_usage.total_tokens += stage.model_usage.total_tokens
        run_usage.usage_available = run_usage.usage_available and stage.model_usage.usage_available
        self._report.retries += stage.retries
        self._report.tool_calls += stage.tool_calls

    def _require_stage(self, event: str) -> StageMetrics:
        if self._current_stage is None:
            raise RuntimeError(f"Cannot record {event} outside an active tracker stage.")
        return self._current_stage

    def _write_performance(self) -> None:
        """Atomically refresh the aggregate report after each completed stage."""

        temporary_path = self.performance_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(self._report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.performance_path)

    @staticmethod
    def _require_non_negative(name: str, value: int) -> None:
        if value < 0:
            raise ValueError(f"{name} must be non-negative.")
