"""Command-line argument definitions without execution logic."""

from __future__ import annotations

import argparse
from pathlib import Path

_CONTEXT_MODES = ("full-corpus", "bm25", "llm-expanded-bm25")


def _positive_int(value: str) -> int:
    """Parse a command-line integer that must be at least one."""

    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    """Parse a command-line number that must be positive."""

    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", type=Path, default=Path("runs"))
    parser.add_argument("--quiet", action="store_true")


def _set_operation(
    parser: argparse.ArgumentParser,
    command_name: str,
    operation: str = "unimplemented",
) -> None:
    parser.set_defaults(command_name=command_name, operation=operation)
    _add_runtime_options(parser)


def _add_documentation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--context-mode", choices=_CONTEXT_MODES, default="bm25")
    parser.add_argument(
        "--corpus-input",
        type=Path,
        help="Load manifest.json, documents.jsonl, and chunks.jsonl from a frozen corpus.",
    )
    parser.add_argument("--model")
    parser.add_argument("--web-recording", type=Path)
    parser.add_argument("--model-recording", type=Path)
    parser.add_argument(
        "--max-discovery-steps",
        type=_positive_int,
        default=2,
        help="Maximum model-directed documentation discovery steps (default: 2).",
    )
    parser.add_argument(
        "--short-site-name",
        "--site-name",
        dest="site_name",
        help="Site name for discovery and required when live collection is needed.",
    )
    parser.add_argument(
        "--discovery-note",
        help="Optional text that helps the model find target-site documentation.",
    )
    parser.add_argument(
        "--discovery-keyword",
        action="append",
        default=[],
        help="Additional documentation-search keyword; may be repeated.",
    )
    parser.add_argument(
        "--documentation-domain",
        action="append",
        default=[],
        help="Official documentation domain when it differs from the login domain.",
    )
    _add_live_measurement_hints(parser)


def _add_live_measurement_hints(parser: argparse.ArgumentParser) -> None:
    """Add optional exact hints used only when collecting a live measurement."""

    parser.add_argument("--condor-pool", help="Optional HTCondor collector hostname.")
    parser.add_argument(
        "--storage-path",
        action="append",
        default=[],
        metavar="ROLE=/ABSOLUTE/PATH",
        help="Exact additional storage path; repeat as needed.",
    )
    parser.add_argument(
        "--storage-root",
        action="append",
        default=[],
        help="Root under which exact username and group paths are checked.",
    )


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
        help="Load supplied measurements or collect them from a live HPC login node.",
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
    profile_build.add_argument("--measurements", type=Path)
    profile_build.add_argument("--pilot-results", type=Path)
    profile_build.add_argument(
        "--run-pilots",
        action="store_true",
        help="Explicitly approve and submit the predefined pilot during a live Slurm run.",
    )
    profile_build.add_argument("--pilot-start-port", type=int, default=9000)
    profile_build.add_argument(
        "--pilot-coordination-timeout",
        type=_positive_float,
        default=3600.0,
    )
    profile_build.add_argument("--profile", type=Path)
    profile_build.add_argument("--profile-url")
    profile_build.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    _add_documentation_options(profile_build)
    _set_operation(profile_build, "profile build", "profile_build")

    profile_validate = profile_sub.add_parser("validate", help="Validate a site profile.")
    profile_validate.add_argument("--profile", type=Path, required=True)
    _set_operation(profile_validate, "profile validate")

    profile_show = profile_sub.add_parser("show", help="Display profile state and freshness.")
    profile_show.add_argument("--site-id", required=True)
    profile_show.add_argument("--profile", type=Path)
    profile_show.add_argument("--profile-url")
    _set_operation(profile_show, "profile show")

    evidence = subcommands.add_parser("evidence", help="Capture login or pilot evidence.")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)

    capture_login = evidence_sub.add_parser(
        "capture-login", help="Capture safe login-node measurements."
    )
    capture_login.add_argument(
        "--short-site-name",
        "--site-name",
        dest="site_name",
        required=True,
        help="Short name people use for the HPC site.",
    )
    capture_login.add_argument("--keyword", action="append", default=[])
    capture_login.add_argument("--documentation-domain", action="append", default=[])
    _add_live_measurement_hints(capture_login)
    capture_login.add_argument(
        "--output",
        type=Path,
        default=Path("login-measurements.json"),
    )
    _set_operation(capture_login, "evidence capture-login", "capture_login")

    run_pilots = evidence_sub.add_parser("run-pilots", help="Run predefined bounded pilot jobs.")
    run_pilots.add_argument("--site-id", required=True)
    run_pilots.add_argument("--login-host", required=True)
    run_pilots.add_argument(
        "--storage",
        action="append",
        required=True,
        metavar="NAME=/PATH",
    )
    run_pilots.add_argument("--output", type=Path, required=True)
    run_pilots.add_argument("--scheduler", choices=("slurm", "htcondor"), required=True)
    run_pilots.add_argument("--start-port", type=int, default=9000)
    run_pilots.add_argument(
        "--coordination-timeout",
        type=_positive_float,
        default=3600.0,
    )
    run_pilots.add_argument("--slurm-partition")
    run_pilots.add_argument("--slurm-account")
    run_pilots.add_argument(
        "--approve",
        action="store_true",
        required=True,
        help="Confirm that the predefined jobs may be submitted.",
    )
    run_pilots.add_argument(
        "--pilot-runs-dir",
        type=Path,
        default=Path("pilot-runs"),
    )
    _set_operation(run_pilots, "evidence run-pilots", "run_pilots")

    evaluate = subcommands.add_parser("evaluate", help="Run isolated evaluation workflows.")
    evaluate_sub = evaluate.add_subparsers(dest="evaluate_command", required=True)

    documentation = evaluate_sub.add_parser(
        "documentation", help="Evaluate documentation discovery and extraction only."
    )
    documentation.add_argument("--measurements", type=Path, required=True)
    documentation.add_argument("--site-mode", choices=("simulate", "live"), default="simulate")
    documentation.add_argument("--model-mode", choices=("live", "simulate"), default="live")
    documentation.add_argument("--web-mode", choices=("live", "simulate"), default="live")
    documentation.add_argument("--output-dir", type=Path, default=Path("artifacts/documentation"))
    _add_documentation_options(documentation)
    _set_operation(documentation, "evaluate documentation", "evaluate_documentation")

    preflight = subcommands.add_parser(
        "preflight", help="Compare a backpack with a candidate site profile."
    )
    preflight.add_argument("--backpack", type=Path, required=True)
    preflight.add_argument("--site-profile", type=Path, required=True)
    _set_operation(preflight, "preflight")

    return parser
