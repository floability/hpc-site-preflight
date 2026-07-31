"""Build a site-specific Floability command from deterministic checks."""

from __future__ import annotations

import shlex
from typing import Literal, cast

from hpc_site_preflight.backpack.models import BackpackRequirements
from hpc_site_preflight.preflight.compatibility import check_compatibility
from hpc_site_preflight.preflight.models import ExecutionPlan, PreflightResult
from hpc_site_preflight.profiles.models import SiteProfile

_REPLACED_OPTIONS = {
    "--batch-options",
    "--batch-type",
    "--manager-ports",
    "--worker-transfer-ports",
}


def plan_preflight(
    requirements: BackpackRequirements,
    profile: SiteProfile,
    scheduler_values: dict[str, str] | None = None,
) -> PreflightResult:
    """Return ready, blocked, or unknown without submitting a workflow."""

    decision = check_compatibility(requirements, profile, scheduler_values or {})
    result = cast(Literal["ready", "blocked", "unknown"], decision.result)
    execution_plan: ExecutionPlan | None = None
    if result == "ready":
        assert decision.selected_resource is not None
        command = _base_command(requirements.original_command)
        batch_type = "condor" if profile.scheduler_type == "htcondor" else "slurm"
        command.extend(["--batch-type", batch_type])

        if profile.scheduler_type == "slurm" and decision.batch_arguments:
            command.extend(["--batch-options", shlex.join(decision.batch_arguments)])
        elif profile.scheduler_type == "htcondor" and requirements.batch_options:
            command.extend(["--batch-options", requirements.batch_options])

        if requirements.action in {"run", "execute"} and decision.manager_ports:
            command.extend(
                ["--manager-ports", ",".join(str(port) for port in decision.manager_ports)]
            )
        if decision.transfer_port_range is not None:
            command.extend(["--worker-transfer-ports", decision.transfer_port_range])

        execution_plan = ExecutionPlan(
            selected_resource=decision.selected_resource,
            scheduler_arguments=decision.batch_arguments,
            floability_command=command,
            floability_command_text=shlex.join(command),
            settings={
                "maximum_workers": requirements.resources.maximum_workers,
                "manager_ports": decision.manager_ports,
                "worker_transfer_port_range": decision.transfer_port_range,
                "condor_requirements": requirements.condor_requirements,
            },
        )

    return PreflightResult(
        site_id=profile.site_id,
        workflow_id=requirements.backpack_id,
        result=result,
        requirements=requirements,
        execution_plan=execution_plan,
        issues=decision.issues,
    )


def _base_command(arguments: list[str]) -> list[str]:
    """Remove options that the execution plan replaces with validated values."""

    result: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        name = token.partition("=")[0]
        if name not in _REPLACED_OPTIONS:
            result.append(token)
            index += 1
            continue
        if "=" not in token and index + 1 < len(arguments):
            index += 2
        else:
            index += 1
    return result
