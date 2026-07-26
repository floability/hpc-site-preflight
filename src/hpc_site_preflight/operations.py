"""Top-level command operations called by the CLI dispatcher."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import cast

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
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.profiles.documentation import apply_documentation
from hpc_site_preflight.providers.base import ModelProvider, ModelProviderName
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


def build_profile(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Build profile artifacts from supplied or newly collected login measurements.

    The operation builds a measurement-backed profile, runs documentation analysis, applies
    accepted findings, and writes the profile and evidence artifacts. It returns no in-memory
    result.
    """

    measurements, measurement_path = _resolve_measurements(args, tracker)
    with tracker.stage("measurement_profile_build"):
        profile, report = compile_profile(measurements)

    documentation = _build_documentation(
        args,
        measurements,
        tracker,
        measurement_path=measurement_path,
    )
    with tracker.stage("documentation_profile_apply"):
        profile, report = apply_documentation(profile, report, documentation)

    profile_path = args.output_dir / "site-profile.json"
    report_path = args.output_dir / "evidence-report.json"
    documentation_path = args.output_dir / "documentation-evidence.json"
    with tracker.stage("profile_artifact_write"):
        write_json(profile_path, profile.model_dump(mode="json"))
        write_json(report_path, report.model_dump(mode="json"))
        write_json(documentation_path, documentation.model_dump(mode="json"))
        tracker.add_artifact(kind="site_profile", path=profile_path)
        tracker.add_artifact(kind="evidence_report", path=report_path)
        tracker.add_artifact(kind="documentation_evidence", path=documentation_path)

    if not args.quiet:
        print(f"Profile:  {profile_path}")
        print(f"Evidence: {report_path}")


def capture_login_measurements(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Collect one structured login-measurement file without building a profile."""

    provider = LiveMeasurementProvider(
        args.site_name,
        keywords=args.keyword,
        documentation_domains=args.documentation_domain,
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
        web_backend = _web_backend(
            web_mode,
            web_path,
            measurements.site_facts.documentation_domains,
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
