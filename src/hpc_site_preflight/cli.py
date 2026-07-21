"""CLI process lifecycle and operation dispatch."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from dotenv import load_dotenv

from hpc_site_preflight.cli_parser import build_parser
from hpc_site_preflight.config import AppConfig
from hpc_site_preflight.exceptions import PreflightError
from hpc_site_preflight.operations import (
    build_profile,
    evaluate_documentation,
    run_unimplemented,
)
from hpc_site_preflight.reporting.tracker import RunTracker

Operation = Callable[[argparse.Namespace, RunTracker], None]

# _OPERATIONS: dict[str, Operation] = {
#     "profile_build": build_profile,
#     "evaluate_documentation": evaluate_documentation,
#     "unimplemented": run_unimplemented,
# }


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, dispatch one operation, and always finalize its report."""

    load_dotenv()
    args = build_parser().parse_args(argv)
    config = AppConfig(run_dir=args.run_dir, quiet=args.quiet)
    
    tracker = RunTracker(
        command=args.command_name,
        mode=_mode_summary(args),
        run_root=config.run_dir,
        quiet=config.quiet,
    )

    exit_code = 0
    try:
        # _OPERATIONS[args.operation](args, tracker)
        
        if args.operation == "profile_build":
            build_profile(args, tracker)
        elif args.operation == "evaluate_documentation":
            evaluate_documentation(args, tracker)
        else:
            run_unimplemented(args, tracker)
        
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
