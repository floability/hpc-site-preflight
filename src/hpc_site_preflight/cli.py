"""Command-line entry point and Milestone 1 command skeleton."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from dotenv import load_dotenv

from hpc_site_preflight.config import AppConfig
from hpc_site_preflight.documentation.extraction import empty_documentation
from hpc_site_preflight.documentation.models import (
    ContextMode,
    DocumentationEvidence,
    RuntimeMode,
)
from hpc_site_preflight.documentation.policy_agent_adapter import PolicyAgentAdapter
from hpc_site_preflight.documentation.web import (
    LiveWebBackend,
    RecordedWebBackend,
    WebBackend,
)
from hpc_site_preflight.exceptions import (
    ConfigurationError,
    DocumentationError,
    FeatureNotImplementedError,
    ModelProviderError,
    PreflightError,
)
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.measurements.simulated import SimulatedMeasurementProvider
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.providers.base import ModelProvider, ModelProviderName
from hpc_site_preflight.providers.recorded import RecordedModelProvider
from hpc_site_preflight.providers.registry import (
    create_live_model_provider,
    provider_for_model,
)
from hpc_site_preflight.reporting.artifacts import write_json
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.loader import load_site_info
from hpc_site_preflight.site_info.models import SiteInfo

_CONTEXT_MODES = ("full-corpus", "bm25", "schema-expanded-bm25")


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", type=Path, default=Path("runs"))
    parser.add_argument("--quiet", action="store_true")


def _set_handler(parser: argparse.ArgumentParser, command_name: str) -> None:
    parser.set_defaults(command_name=command_name, handler=_placeholder_handler)
    _add_runtime_options(parser)


def build_parser() -> argparse.ArgumentParser:
    """Build the complete CLI parser without running any pipeline stage."""

    parser = argparse.ArgumentParser(
        prog="hpc-site-preflight",
        description="Build evidence-backed HPC site profiles and preflight portable workflows.",
    )
    subcommands = parser.add_subparsers(dest="command_group", required=True)

    profile = subcommands.add_parser("profile", help="Build, validate, or inspect site profiles.")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)

    profile_build = profile_sub.add_parser("build", help="Construct or update a site profile.")
    profile_build.add_argument(
        "--site-mode",
        choices=("simulate", "live"),
        default="simulate",
        help="Use supplied site files or collect from a live HPC site.",
    )
    profile_build.add_argument(
        "--model-mode",
        choices=("live", "simulate"),
        default="live",
        help="Call the configured model or replay recorded responses.",
    )
    profile_build.add_argument(
        "--web-mode",
        choices=("live", "simulate"),
        default="live",
        help="Search and fetch official documentation or replay recorded pages.",
    )
    profile_build.add_argument("--site-info", type=Path, required=True)
    profile_build.add_argument("--measurements", type=Path)
    profile_build.add_argument("--pilot-results", type=Path)
    profile_build.add_argument("--profile", type=Path)
    profile_build.add_argument("--profile-url")
    profile_build.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    profile_build.add_argument("--context-mode", choices=_CONTEXT_MODES, default="bm25")
    profile_build.add_argument("--model")
    profile_build.add_argument("--web-recording", type=Path)
    profile_build.add_argument("--model-recording", type=Path)
    _set_handler(profile_build, "profile build")
    profile_build.set_defaults(handler=_profile_build_handler)

    profile_validate = profile_sub.add_parser("validate", help="Validate a site profile.")
    profile_validate.add_argument("--profile", type=Path, required=True)
    _set_handler(profile_validate, "profile validate")

    profile_show = profile_sub.add_parser("show", help="Display profile state and freshness.")
    profile_show.add_argument("--site-id", required=True)
    profile_show.add_argument("--profile", type=Path)
    profile_show.add_argument("--profile-url")
    _set_handler(profile_show, "profile show")

    evidence = subcommands.add_parser("evidence", help="Capture login or pilot evidence.")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)

    capture_login = evidence_sub.add_parser(
        "capture-login", help="Capture safe login-node measurements."
    )
    capture_login.add_argument("--output", type=Path, required=True)
    capture_login.add_argument("--scheduler", choices=("auto", "slurm", "htcondor"), default="auto")
    _set_handler(capture_login, "evidence capture-login")

    run_pilots = evidence_sub.add_parser("run-pilots", help="Run predefined bounded pilot jobs.")
    run_pilots.add_argument("--site-info", type=Path, required=True)
    run_pilots.add_argument("--output", type=Path, required=True)
    run_pilots.add_argument("--scheduler", choices=("slurm", "htcondor"), required=True)
    _set_handler(run_pilots, "evidence run-pilots")

    evaluate = subcommands.add_parser("evaluate", help="Run isolated evaluation workflows.")
    evaluate_sub = evaluate.add_subparsers(dest="evaluate_command", required=True)

    documentation = evaluate_sub.add_parser(
        "documentation", help="Evaluate documentation discovery and extraction only."
    )
    documentation.add_argument("--site-info", type=Path, required=True)
    documentation.add_argument("--measurements", type=Path, required=True)
    documentation.add_argument("--site-mode", choices=("simulate", "live"), default="simulate")
    documentation.add_argument("--model-mode", choices=("live", "simulate"), default="live")
    documentation.add_argument("--web-mode", choices=("live", "simulate"), default="live")
    documentation.add_argument("--context-mode", choices=_CONTEXT_MODES, default="bm25")
    documentation.add_argument("--model")
    documentation.add_argument("--web-recording", type=Path)
    documentation.add_argument("--model-recording", type=Path)
    documentation.add_argument("--output-dir", type=Path, default=Path("artifacts/documentation"))
    _set_handler(documentation, "evaluate documentation")
    documentation.set_defaults(handler=_documentation_evaluation_handler)

    preflight = subcommands.add_parser(
        "preflight", help="Compare a backpack with a candidate site profile."
    )
    preflight.add_argument("--backpack", type=Path, required=True)
    preflight.add_argument("--site-profile", type=Path, required=True)
    _set_handler(preflight, "preflight")

    return parser


def _placeholder_handler(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Record command dispatch, then fail explicitly until its milestone is implemented."""

    with tracker.stage("command_dispatch"):
        raise FeatureNotImplementedError(
            f"The '{args.command_name}' command is defined but not implemented yet. "
            "Follow MILESTONES.md and implement one milestone at a time."
        )


def _profile_build_handler(args: argparse.Namespace, tracker: RunTracker) -> None:
    """Build a simulated profile from measurements and documentation."""

    if args.site_mode != "simulate":
        raise FeatureNotImplementedError("Live site collection is deferred until Phase E.")
    if args.measurements is None:
        raise ConfigurationError("Simulated site mode requires --measurements.")

    with tracker.stage("site_info_load"):
        site = load_site_info(args.site_info)

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


def _documentation_evaluation_handler(
    args: argparse.Namespace,
    tracker: RunTracker,
) -> None:
    """Run the documentation subsystem without compiling a site profile."""

    if args.site_mode != "simulate":
        raise FeatureNotImplementedError("Live site collection is deferred until Phase E.")

    with tracker.stage("site_info_load"):
        site = load_site_info(args.site_info)
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
    site: SiteInfo,
    measurements: MeasurementBundle,
    tracker: RunTracker,
) -> DocumentationEvidence:
    """Resolve simulated inputs and run the linear documentation adapter."""

    web_path = args.web_recording or args.site_info.parent / "documentation-web.json"
    model_path = args.model_recording or args.site_info.parent / "documentation-model.json"
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
        storage_names = {
            observation.path.split("/")[4]
            for observation in measurements.common
            if observation.path.startswith("/facts/storage/filesystems/")
            and observation.path.endswith("/path")
        }
        return empty_documentation(
            site_id=site.site_id,
            context_mode=cast(ContextMode, args.context_mode),
            model_mode=model_mode,
            model_provider=model_provider_name,
            model=model_name,
            web_mode=web_mode,
            scheduler=site.scheduler,
            storage_names=storage_names,
            reason=str(exc),
        )
    adapter = PolicyAgentAdapter(
        measurements=measurements,
        model_provider=model_provider,
        web_backend=web_backend,
        corpus_directory=args.output_dir / "corpus",
        model_mode=model_mode,
        model_provider_name=model_provider_name,
        model=model_name,
        web_mode=web_mode,
    )
    return adapter.build(
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
    model = (
        argument
        or os.getenv("HPC_SITE_PREFLIGHT_MODEL")
        or os.getenv("OPENAI_MODEL")
    )
    if not model:
        raise ConfigurationError(
            "--model or HPC_SITE_PREFLIGHT_MODEL is required when --model-mode live is selected."
        )
    return model


def _web_backend(mode: RuntimeMode, recording: Path, site: SiteInfo) -> WebBackend:
    if mode == "simulate":
        return RecordedWebBackend.from_path(recording)
    return LiveWebBackend(site.documentation.allowed_domains)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run one command, and always emit a performance report."""

    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig(run_dir=args.run_dir, quiet=args.quiet)
    tracker = RunTracker(
        command=args.command_name,
        mode=_mode_summary(args),
        run_root=config.run_dir,
        quiet=config.quiet,
    )

    exit_code = 0
    try:
        args.handler(args, tracker)
    except PreflightError as exc:
        tracker.record_run_error(exc)
        if not config.quiet:
            print(f"error: {exc}", file=sys.stderr)
        exit_code = 2
    except Exception as exc:  # Defensive: the tracker must still write a failed report.
        tracker.record_run_error(exc)
        if not config.quiet:
            print(f"unexpected error: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        tracker.finalize(status="completed" if exit_code == 0 else "failed")

    return exit_code


def _mode_summary(args: argparse.Namespace) -> str | None:
    if not hasattr(args, "site_mode"):
        return None
    return f"site={args.site_mode}, model={args.model_mode}, web={args.web_mode}"
