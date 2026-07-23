"""Command-line argument definitions without execution logic."""

from __future__ import annotations

import argparse
from pathlib import Path

_CONTEXT_MODES = ("full-corpus", "bm25", "llm-expanded-bm25")


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
    parser.add_argument("--model")
    parser.add_argument("--web-recording", type=Path)
    parser.add_argument("--model-recording", type=Path)
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
    capture_login.add_argument(
        "--output",
        type=Path,
        default=Path("login-measurements.json"),
    )
    _set_operation(capture_login, "evidence capture-login", "capture_login")

    run_pilots = evidence_sub.add_parser("run-pilots", help="Run predefined bounded pilot jobs.")
    run_pilots.add_argument("--measurements", type=Path, required=True)
    run_pilots.add_argument("--output", type=Path, required=True)
    run_pilots.add_argument("--scheduler", choices=("slurm", "htcondor"), required=True)
    _set_operation(run_pilots, "evidence run-pilots")

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
