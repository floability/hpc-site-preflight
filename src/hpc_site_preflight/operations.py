"""Top-level command operations called by the CLI dispatcher."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from hpc_site_preflight.backpack.loader import load_backpack
from hpc_site_preflight.documentation.extraction import empty_documentation
from hpc_site_preflight.documentation.models import (
    ContextMode,
    DocumentationEvidence,
    RuntimeMode,
)
from hpc_site_preflight.documentation.pipeline import DocumentationPipeline
from hpc_site_preflight.documentation.tools import LiveWebBackend, RecordedWebBackend, WebBackend
from hpc_site_preflight.exceptions import (
    ConfigurationError,
    DocumentationError,
    FeatureNotImplementedError,
    ModelProviderError,
)
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.measurements.live import LiveMeasurementProvider
from hpc_site_preflight.measurements.simulated import SimulatedMeasurementProvider
from hpc_site_preflight.preflight.models import (
    PreflightNarration,
    PreflightResult,
)
from hpc_site_preflight.preflight.planner import plan_preflight
from hpc_site_preflight.probes.base import PilotInputs, PilotResultBundle
from hpc_site_preflight.probes.htcondor import HTCondorPilot
from hpc_site_preflight.probes.live import LivePilotProvider
from hpc_site_preflight.probes.simulated import SimulatedPilotProvider
from hpc_site_preflight.probes.slurm import SlurmPilot
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.profiles.documentation import apply_documentation
from hpc_site_preflight.profiles.models import SiteProfile
from hpc_site_preflight.profiles.pilots import apply_pilot_results
from hpc_site_preflight.providers.base import (
    ModelProvider,
    ModelProviderName,
    StructuredModelRequest,
)
from hpc_site_preflight.providers.recorded import RecordedModelProvider
from hpc_site_preflight.providers.registry import create_live_model_provider, provider_for_model
from hpc_site_preflight.reporting.artifacts import write_json
from hpc_site_preflight.reporting.tracker import RunTracker


def run_unimplemented(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Record command dispatch and raise an explicit error for an unfinished operation.

    The parsed arguments supply the command name; no operation result is returned.
    """

    with tracker.stage("command_dispatch"):
        raise FeatureNotImplementedError(
            f"The '{args.command_name}' command is defined but not implemented yet. "
            "Follow MILESTONES.md and implement one milestone at a time."
        )


def preflight_workflow(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Adapt a Floability command and compare it with a supplied site profile."""

    with tracker.stage("workflow_requirement_load"):
        requirements = load_backpack(args.backpack, args.floability_command)
    with tracker.stage("site_profile_load"):
        try:
            profile = SiteProfile.model_validate_json(args.site_profile.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"Could not read site profile: {args.site_profile}") from exc
        except ValidationError as exc:
            raise ConfigurationError(
                f"Site profile failed {exc.error_count()} contract validation(s)."
            ) from exc

    scheduler_values = _scheduler_values(args.scheduler_value)
    with tracker.stage("deterministic_preflight"):
        result = plan_preflight(requirements, profile, scheduler_values)

    if args.explain_with_model:
        result = _narrate_preflight(args, result, tracker)

    with tracker.stage("preflight_artifact_write"):
        write_json(args.output, result.model_dump(mode="json"))
        tracker.add_artifact(kind="preflight_result", path=args.output)
    if not args.quiet:
        print(f"Preflight result: {args.output}")
        print(f"Decision: {result.result}")
        if result.execution_plan is not None:
            print(f"Command:  {result.execution_plan.floability_command_text}")
        for issue in result.issues:
            print(f"{issue.severity}: {issue.reason}")


def _scheduler_values(values: list[str]) -> dict[str, str]:
    """Parse repeatable NAME=VALUE inputs without interpreting their values."""

    result: dict[str, str] = {}
    for item in values:
        name, separator, value = item.partition("=")
        if not separator or not name.strip() or not value.strip():
            raise ConfigurationError("--scheduler-value must use NAME=VALUE.")
        result[name.strip()] = value.strip()
    return result


def _narrate_preflight(
    args: argparse.Namespace,
    result: PreflightResult,
    tracker: RunTracker,
) -> PreflightResult:
    """Append optional prose while preventing the model from changing the result."""

    mode = cast(RuntimeMode, args.model_mode)
    provider: ModelProvider
    if mode == "simulate":
        if args.model_recording is None:
            raise ConfigurationError("Simulated preflight narration requires --model-recording.")
        provider = RecordedModelProvider.from_path(args.model_recording)
    else:
        model = _model_name(mode, args.model)
        assert model is not None
        provider = create_live_model_provider(model)

    request = StructuredModelRequest(
        system_prompt=(
            "Explain the supplied deterministic HPC preflight result in concise plain language. "
            "Do not change its status, values, command, issues, or remediation."
        ),
        user_prompt=json.dumps(result.model_dump(mode="json"), indent=2),
        output_name="preflight_narration",
        output_description="Return a concise human-readable explanation of the fixed result.",
    )
    narration = provider.generate_structured(request, PreflightNarration, tracker)
    return result.model_copy(update={"narrative": narration.message})


def build_profile(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Build profile artifacts from supplied or newly collected login measurements.

    The operation builds a measurement-backed profile, runs documentation analysis, applies
    accepted findings, and writes the profile and evidence artifacts. It returns no in-memory
    result.
    """

    measurements, measurement_path = _resolve_measurements(args, tracker)
    with tracker.stage("measurement_profile_build"):
        profile, report = compile_profile(
            measurements,
            evidence_id=f"report-{tracker.run_id}",
        )

    documentation = _build_documentation(
        args,
        measurements,
        tracker,
        measurement_path=measurement_path,
    )
    with tracker.stage("documentation_profile_apply"):
        profile, report = apply_documentation(profile, report, documentation)

    pilots = _resolve_pilots(args, measurements, tracker)
    if pilots is not None:
        with tracker.stage("pilot_profile_apply"):
            profile, report = apply_pilot_results(profile, report, pilots)

    profile_path = args.output_dir / "site-profile.json"
    report_path = args.output_dir / f"evidence-{profile.evidence_id}.json"
    documentation_path = args.output_dir / "documentation-evidence.json"
    pilot_path = args.output_dir / "pilot-evidence.json"
    with tracker.stage("profile_artifact_write"):
        write_json(profile_path, profile.model_dump(mode="json"))
        write_json(report_path, report.model_dump(mode="json"))
        write_json(documentation_path, documentation.model_dump(mode="json"))
        if pilots is not None:
            write_json(pilot_path, pilots.model_dump(mode="json"))
        tracker.add_artifact(kind="site_profile", path=profile_path)
        tracker.add_artifact(kind="evidence_report", path=report_path)
        tracker.add_artifact(kind="documentation_evidence", path=documentation_path)
        if pilots is not None:
            tracker.add_artifact(kind="pilot_evidence", path=pilot_path)

    if not args.quiet:
        print(f"Profile:  {profile_path}")
        print(f"Evidence: {report_path}")


def _resolve_pilots(
    args: argparse.Namespace,
    measurements: MeasurementBundle,
    tracker: RunTracker,
) -> PilotResultBundle | None:
    """Load recorded pilot evidence or run the approved live Slurm pilot."""

    if not args.run_pilots:
        if args.pilot_results is not None:
            raise ConfigurationError("--pilot-results requires --run-pilots.")
        return None
    if args.site_mode == "simulate":
        if args.pilot_results is None:
            raise ConfigurationError(
                "Simulated site mode requires --pilot-results with --run-pilots."
            )
        return SimulatedPilotProvider(args.pilot_results).collect(measurements, tracker)
    if args.pilot_results is not None:
        raise ConfigurationError("Live site mode cannot use --pilot-results.")
    provider = LivePilotProvider(
        args.output_dir / "pilot-evidence.json",
        args.output_dir / "pilot-runs",
        start_port=args.pilot_start_port,
        coordination_timeout=args.pilot_coordination_timeout,
    )
    return provider.collect(measurements, tracker)


def run_pilots(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Run one explicitly approved scheduler pilot from direct CLI inputs."""

    storage: dict[str, str] = {}
    for value in args.storage:
        name, separator, raw_path = value.partition("=")
        path = Path(raw_path).expanduser()
        if not separator or not name or not path.is_absolute():
            raise ConfigurationError("--storage must use NAME=/absolute/path.")
        storage[name] = str(path)
    inputs = PilotInputs(
        site_id=args.site_id,
        scheduler=args.scheduler,
        login_host=args.login_host,
        storage=storage,
        output=args.output,
        runs_dir=args.pilot_runs_dir,
        start_port=args.start_port,
        coordination_timeout=args.coordination_timeout,
        partition=args.slurm_partition,
        account=args.slurm_account,
    )
    runner = SlurmPilot() if args.scheduler == "slurm" else HTCondorPilot()
    with tracker.stage("approved_pilot"):
        result = runner.run(inputs)
        tracker.add_artifact(kind="pilot_result", path=args.output)
    if not args.quiet:
        print(f"Pilot result: {args.output}")
        print(f"Pilot status: {result.status}")


def capture_login_measurements(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Collect one structured login-measurement file without building a profile."""

    provider = LiveMeasurementProvider(
        args.site_name,
        keywords=args.keyword,
        documentation_domains=args.documentation_domain,
        condor_pool=args.condor_pool,
        storage_paths=args.storage_path,
        storage_roots=args.storage_root,
    )
    measurements = provider.collect(tracker)
    with tracker.stage("login_measurement_write"):
        write_json(args.output, measurements.model_dump(mode="json"))
        tracker.add_artifact(kind="login_measurements", path=args.output)
    if not args.quiet:
        print(f"Login measurements: {args.output}")


def evaluate_documentation(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Evaluate documentation using CLI inputs without compiling a site profile.

    The operation loads the descriptor and measurements, runs discovery and extraction, and writes
    one documentation-evidence JSON artifact. It returns no in-memory result.
    """

    measurements, measurement_path = _resolve_measurements(args, tracker)
    documentation = _build_documentation(
        args,
        measurements,
        tracker,
        measurement_path=measurement_path,
    )
    output_path = args.output_dir / "documentation-evidence.json"
    with tracker.stage("documentation_artifact_write"):
        write_json(output_path, documentation.model_dump(mode="json"))
        tracker.add_artifact(kind="documentation_evidence", path=output_path)
    if not args.quiet:
        print(f"Documentation evidence: {output_path}")


def _build_documentation(
    args: argparse.Namespace,
    measurements: MeasurementBundle,
    tracker: RunTracker,
    *,
    measurement_path: Path,
) -> DocumentationEvidence:
    """Run documentation analysis for one validated site and measurement bundle.

    Runtime arguments select live or recorded model and web providers. Returns discovered and
    extracted documentation evidence, or an explicit empty partial result if setup fails.
    """

    web_path = args.web_recording or measurement_path.parent / "documentation-web.json"
    model_path = args.model_recording or measurement_path.parent / "documentation-model.json"

    model_mode = cast(RuntimeMode, args.model_mode)
    web_mode = cast(RuntimeMode, args.web_mode)
    model_name = _model_name(model_mode, args.model)
    model_provider_name: ModelProviderName = (
        "recorded" if model_name is None else provider_for_model(model_name)
    )
    tracker.progress(
        f"Model provider: {model_provider_name}; model: {model_name or 'recorded responses'}"
    )
    try:
        web_backend = (
            None
            if args.corpus_input is not None
            else _web_backend(
                web_mode,
                web_path,
                measurements.site_facts.documentation_domains,
            )
        )
        model_provider = _model_provider(model_mode, model_name, model_path)
    except (DocumentationError, ModelProviderError) as exc:
        return empty_documentation(
            site_id=measurements.site_id,
            context_mode=cast(ContextMode, args.context_mode),
            model_mode=model_mode,
            model_provider=model_provider_name,
            model=model_name,
            web_mode=web_mode,
            scheduler=measurements.scheduler_type,
            storage_names=measurements.storage_names,
            reason=str(exc),
        )

    pipeline = DocumentationPipeline(
        measurements=measurements,
        model_provider=model_provider,
        web_backend=web_backend,
        corpus_directory=args.output_dir / "corpus",
        model_mode=model_mode,
        model_provider_name=model_provider_name,
        model=model_name,
        web_mode=web_mode,
        discovery_site_name=args.site_name,
        discovery_note=args.discovery_note,
        discovery_keywords=args.discovery_keyword,
        max_discovery_steps=args.max_discovery_steps,
        corpus_input=args.corpus_input,
    )

    return pipeline.build(tracker, context_mode=cast(ContextMode, args.context_mode))


def _resolve_measurements(
    args: argparse.Namespace,
    tracker: RunTracker,
) -> tuple[MeasurementBundle, Path]:
    """Load supplied measurements or collect them for a live site run."""

    if args.measurements is not None:
        return SimulatedMeasurementProvider(args.measurements).collect(tracker), args.measurements
    if args.site_mode == "simulate":
        raise ConfigurationError("Simulated site mode requires --measurements.")
    if not args.site_name:
        raise ConfigurationError(
            "Live site mode requires --short-site-name when --measurements is omitted."
        )

    provider = LiveMeasurementProvider(
        args.site_name,
        keywords=args.discovery_keyword,
        documentation_domains=args.documentation_domain,
        condor_pool=args.condor_pool,
        storage_paths=args.storage_path,
        storage_roots=args.storage_root,
    )
    measurements = provider.collect(tracker)
    path = args.output_dir / "login-measurements.json"
    with tracker.stage("login_measurement_write"):
        write_json(path, measurements.model_dump(mode="json"))
        tracker.add_artifact(kind="login_measurements", path=path)
    return measurements, path


def _model_provider(mode: RuntimeMode, model: str | None, recording: Path) -> ModelProvider:
    """Return a recorded provider for simulation or a live adapter for the model name."""

    if mode == "simulate":
        return RecordedModelProvider.from_path(recording)
    assert model is not None
    return create_live_model_provider(model)


def _model_name(mode: RuntimeMode, argument: str | None) -> str | None:
    """Return the CLI or environment model name for live mode and ``None`` for simulation."""

    if mode == "simulate":
        return None
    model = argument or os.getenv("HPC_SITE_PREFLIGHT_MODEL") or os.getenv("OPENAI_MODEL")
    if not model:
        raise ConfigurationError(
            "--model or HPC_SITE_PREFLIGHT_MODEL is required when --model-mode live is selected."
        )
    return model


def _web_backend(mode: RuntimeMode, recording: Path, domains: list[str]) -> WebBackend:
    """Return recorded web data or a live backend bounded to the site's allowed domains."""

    if mode == "simulate":
        return RecordedWebBackend.from_path(recording)
    return LiveWebBackend(domains)
