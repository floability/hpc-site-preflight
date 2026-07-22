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
from hpc_site_preflight.measurements.simulated import SimulatedMeasurementProvider
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.providers.base import ModelProvider, ModelProviderName
from hpc_site_preflight.providers.recorded import RecordedModelProvider
from hpc_site_preflight.providers.registry import create_live_model_provider, provider_for_model
from hpc_site_preflight.reporting.artifacts import write_json
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_descriptor.loader import load_site_descriptor
from hpc_site_preflight.site_descriptor.models import SiteDescriptor


def run_unimplemented(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Fail explicitly for a command whose milestone is not implemented."""

    with tracker.stage("command_dispatch"):
        raise FeatureNotImplementedError(
            f"The '{args.command_name}' command is defined but not implemented yet. "
            "Follow MILESTONES.md and implement one milestone at a time."
        )


def build_profile(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Build a simulated profile from measurements and documentation."""

    if args.site_mode != "simulate":
        raise FeatureNotImplementedError("Live site collection is deferred until Phase E.")
    if args.measurements is None:
        raise ConfigurationError("Simulated site mode requires --measurements.")

    with tracker.stage("site_descriptor_load"):
        site = load_site_descriptor(args.site_descriptor)

    measurements = SimulatedMeasurementProvider(args.measurements).collect(site, tracker)
    documentation = _build_documentation(args, site, measurements, tracker)

    with tracker.stage("measurement_profile_build"):
        profile, report = compile_profile(site, measurements, documentation)

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


def evaluate_documentation(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Run the documentation subsystem without compiling a site profile."""

    if args.site_mode != "simulate":
        raise FeatureNotImplementedError("Live site collection is deferred until Phase E.")

    with tracker.stage("site_descriptor_load"):
        site = load_site_descriptor(args.site_descriptor)
    measurements = SimulatedMeasurementProvider(args.measurements).collect(site, tracker)
    documentation = _build_documentation(args, site, measurements, tracker)
    output_path = args.output_dir / "documentation-evidence.json"
    with tracker.stage("documentation_artifact_write"):
        write_json(output_path, documentation.model_dump(mode="json"))
        tracker.add_artifact(kind="documentation_evidence", path=output_path)
    if not args.quiet:
        print(f"Documentation evidence: {output_path}")


def _build_documentation(
    args: argparse.Namespace,
    site: SiteDescriptor,
    measurements: MeasurementBundle,
    tracker: RunTracker,
) -> DocumentationEvidence:
    """Resolve model and web inputs, then run the documentation pipeline."""

    web_path = args.web_recording or args.site_descriptor.parent / "documentation-web.json"
    model_path = args.model_recording or args.site_descriptor.parent / "documentation-model.json"
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
        web_backend = _web_backend(web_mode, web_path, site)
        model_provider = _model_provider(model_mode, model_name, model_path)
    except (DocumentationError, ModelProviderError) as exc:
        return empty_documentation(
            site_id=site.site_id,
            context_mode=cast(ContextMode, args.context_mode),
            model_mode=model_mode,
            model_provider=model_provider_name,
            model=model_name,
            web_mode=web_mode,
            scheduler=site.scheduler,
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
    )
    return pipeline.build(
        site,
        tracker,
        context_mode=cast(ContextMode, args.context_mode),
    )


def _model_provider(mode: RuntimeMode, model: str | None, recording: Path) -> ModelProvider:
    if mode == "simulate":
        return RecordedModelProvider.from_path(recording)
    assert model is not None
    return create_live_model_provider(model)


def _model_name(mode: RuntimeMode, argument: str | None) -> str | None:
    if mode == "simulate":
        return None
    model = argument or os.getenv("HPC_SITE_PREFLIGHT_MODEL") or os.getenv("OPENAI_MODEL")
    if not model:
        raise ConfigurationError(
            "--model or HPC_SITE_PREFLIGHT_MODEL is required when --model-mode live is selected."
        )
    return model


def _web_backend(mode: RuntimeMode, recording: Path, site: SiteDescriptor) -> WebBackend:
    if mode == "simulate":
        return RecordedWebBackend.from_path(recording)
    return LiveWebBackend(site.documentation.allowed_domains)
