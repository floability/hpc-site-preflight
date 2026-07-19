"""Human-readable stage and final-run summaries."""

from hpc_site_preflight.reporting.models import ModelUsage, RunPerformance, StageMetrics


def _token_value(value: int, usage: ModelUsage) -> str:
    """Render counts only when every provider supplied authoritative usage."""

    return str(value) if usage.usage_available else "unavailable"


def print_stage_summary(
    stage: StageMetrics,
    *,
    run_elapsed: float,
    run_usage: ModelUsage,
) -> None:
    """Print one concise summary after a stage completes or fails."""

    print(f"[{stage.status}] {stage.name}")
    print(f"  Time:          {stage.duration_seconds or 0.0:.2f} s")
    print(f"  Model calls:   {stage.model_usage.requests}")
    print(f"  Tokens:        {_token_value(stage.model_usage.total_tokens, stage.model_usage)}")
    print(
        f"  Run total:     {run_elapsed:.2f} s / "
        f"{_token_value(run_usage.total_tokens, run_usage)} tokens"
    )


def print_run_summary(report: RunPerformance, report_path: str) -> None:
    """Print the final aggregated run report."""

    print("=" * 60)
    print("HPC Site Preflight Run Summary")
    print("=" * 60)
    print(f"Command:       {report.command}")
    print(f"Mode:          {report.mode or 'n/a'}")
    print(f"Status:        {report.status}")
    print(f"Total time:    {report.duration_seconds or 0.0:.2f} s")
    print(f"Model calls:   {report.model_usage.requests}")
    print(f"Input tokens:  {_token_value(report.model_usage.input_tokens, report.model_usage)}")
    print(f"Output tokens: {_token_value(report.model_usage.output_tokens, report.model_usage)}")
    print(f"Total tokens:  {_token_value(report.model_usage.total_tokens, report.model_usage)}")
    print(f"Report:        {report_path}")
    print("=" * 60)
