"""Command-line entry point and Milestone 1 command skeleton."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from hpc_site_preflight.config import AppConfig
from hpc_site_preflight.exceptions import FeatureNotImplementedError, PreflightError
from hpc_site_preflight.reporting.tracker import RunTracker

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
    profile_build.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    profile_build.add_argument("--site-info", type=Path, required=True)
    profile_build.add_argument("--measurements", type=Path)
    profile_build.add_argument("--pilot-results", type=Path)
    profile_build.add_argument("--profile", type=Path)
    profile_build.add_argument("--profile-url")
    _set_handler(profile_build, "profile build")

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
    documentation.add_argument("--context-mode", choices=_CONTEXT_MODES, default="bm25")
    documentation.add_argument("--provider", default="openai")
    documentation.add_argument("--model")
    _set_handler(documentation, "evaluate documentation")

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


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run one command, and always emit a performance report."""

    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig(run_dir=args.run_dir, quiet=args.quiet)
    tracker = RunTracker(
        command=args.command_name,
        mode=getattr(args, "mode", None),
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
