"""Bounded model-assisted query expansion for BM25 retrieval."""

from hpc_site_preflight.documentation.models import QueryExpansionResult
from hpc_site_preflight.documentation.retrieval import base_queries
from hpc_site_preflight.exceptions import ModelProviderError
from hpc_site_preflight.providers.base import ModelProvider, StructuredModelRequest
from hpc_site_preflight.reporting.tracker import RunTracker

_FIELD_DESCRIPTIONS = {
    "allocation_required": "Whether a project or allocation is required to submit jobs.",
    "required_submission_options": "Scheduler options the site requires in a job submission.",
    "guaranteed_runtime": "Whether HTCondor jobs are guaranteed to run to completion.",
    "preemptible": "Whether HTCondor jobs may be displaced or evicted.",
    "submission_host": "The site host where users submit HTCondor jobs.",
    "machine_requirements_supported": (
        "Whether jobs may select machine resources using requirements."
    ),
    "dynamic_slots_enabled": "Whether the HTCondor pool enables dynamic slots.",
    "bulk_submission_supported": "Whether one submit description may enqueue many jobs.",
    "completion_email_supported": "Whether the site sends job-completion email.",
    "maximum_walltime_seconds": "Documented enforced walltime limits for queues or partitions.",
    "maximum_nodes_per_job": "Documented maximum nodes allowed in one partition job.",
    "shared_nodes": "Whether jobs may share nodes in a partition.",
    "gpu_count_per_node": "Documented GPUs installed per node for a partition.",
    "gpu_models": "Documented GPU models available through a partition.",
    "features": "Documented node features or architectures for a partition.",
    "manager_worker_connectivity": "Whether compute workers can connect to a workflow manager.",
    "worker_worker_connectivity": "Whether compute workers can connect directly to each other.",
    "outbound_compute": "Whether compute nodes can make outbound Internet connections.",
    "charging_unit": "The unit used to charge allocation consumption.",
    "charging_model": "How jobs or resources consume allocations, credits, or service units.",
    "filesystem_storage_charged": "Whether filesystem storage consumes allocation.",
    "storage_compute_visible": "Whether a storage location is visible on compute nodes.",
    "storage_compute_readable": "Whether compute jobs may read a storage location.",
    "storage_compute_writable": "Whether compute jobs may write a storage location.",
    "storage_shared_across_compute_nodes": "Whether compute nodes share the same storage.",
    "storage_backup_policy": "Documented backup policy for a storage location.",
    "purge_after_days": "When files on temporary or scratch storage become eligible for deletion.",
    "storage_purge_condition": "Condition that triggers storage purging.",
}

_SYSTEM_PROMPT = """Add concise BM25 query variants for downloaded official HPC documentation.
Return search terms only, not policy answers.
Use the site, scheduler, known resource names, field meaning, and base queries.
Add synonyms and site-specific terminology; do not repeat or replace the base queries.
Return at most two distinct queries for each requested field and no unrequested fields.
Each query must be no more than 24 words."""
_MAX_RESOURCES_PER_FIELD = 20
_MAX_QUERY_WORDS = 24


def expand_queries(
    *,
    site_name: str,
    scheduler: str,
    fields: tuple[str, ...],
    resources_by_field: dict[str, set[str]],
    provider: ModelProvider,
    tracker: RunTracker,
) -> tuple[dict[str, list[str]], str | None]:
    """Request and validate one bounded set of field-level BM25 queries.

    Returns accepted queries grouped by requested field. A failed call returns no expansions and
    an error message, allowing retrieval to fall back to its reviewed base queries.
    """

    prompt = _prompt(site_name, scheduler, fields, resources_by_field)
    tracker.progress(f"Requesting additional BM25 queries for {len(fields)} field(s)")
    try:
        result = provider.generate_structured(
            StructuredModelRequest(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=prompt,
                output_name="expand_retrieval_queries",
                output_description="Submit bounded BM25 queries for the requested fields.",
            ),
            QueryExpansionResult,
            tracker,
        )
    except ModelProviderError as exc:
        tracker.progress("Query expansion failed; using reviewed BM25 queries")
        return {}, f"query expansion failed: {exc}"

    expanded = _validated_queries(result, fields)
    count = sum(len(queries) for queries in expanded.values())
    tracker.progress(f"Accepted {count} additional BM25 query variant(s)")
    return expanded, None


def _prompt(
    site_name: str,
    scheduler: str,
    fields: tuple[str, ...],
    resources_by_field: dict[str, set[str]],
) -> str:
    """Return the bounded site and field context shown to the model."""

    lines = [f"SITE: {site_name}", f"SCHEDULER: {scheduler}", "FIELDS:"]
    for field in fields:
        field_resources = set(
            sorted(resources_by_field.get(field, set()))[:_MAX_RESOURCES_PER_FIELD]
        )
        resources = ", ".join(sorted(field_resources)) or "none"
        lines.extend(
            [
                f"FIELD: {field}",
                f"MEANING: {_FIELD_DESCRIPTIONS[field]}",
                f"KNOWN RESOURCES: {resources}",
                f"BASE QUERIES: "
                f"{' | '.join(base_queries(field, scheduler, field_resources))}",
            ]
        )
    return "\n".join(lines)


def _validated_queries(
    result: QueryExpansionResult,
    fields: tuple[str, ...],
) -> dict[str, list[str]]:
    """Keep at most two distinct queries for each requested field."""

    requested = set(fields)
    expanded: dict[str, list[str]] = {}
    for item in result.queries:
        if item.field not in requested:
            continue
        query = " ".join(item.query.split())
        if len(query.split()) > _MAX_QUERY_WORDS:
            continue
        field_queries = expanded.setdefault(item.field, [])
        if query not in field_queries and len(field_queries) < 2:
            field_queries.append(query)
    return expanded
