"""Deterministic field-level full-corpus and BM25 retrieval."""

import math
import re
from collections import Counter

from hpc_site_preflight.documentation.models import (
    ContextMode,
    ContextSelection,
    CorpusChunk,
    ExtractionGroupName,
    FieldRetrieval,
    RetrievalHit,
)

_COMMON_FIELD_QUERIES: dict[str, tuple[str, ...]] = {
    "allocation_required": (
        "allocation project access required job submission",
        "job submission authorization requirements",
    ),
    "required_submission_options": (
        "required scheduler submission directives job file",
        "mandatory job submission fields options",
    ),
    "guaranteed_runtime": (
        "job guaranteed runtime completion",
        "jobs kicked off must run elsewhere",
    ),
    "preemptible": (
        "jobs preempted evicted kicked off",
        "borrowed machines jobs must run elsewhere",
    ),
    "maximum_walltime_seconds": (
        "partition maximum walltime time limit",
        "queue maximum job duration",
    ),
    "maximum_nodes_per_job": (
        "partition queue maximum nodes per job",
        "job size limit nodes",
    ),
    "shared_nodes": (
        "partition shared nodes whole node",
        "node sharing policy",
        "node-sharing complete node dedicated one user",
        "all GPU nodes are shared shared queues node-exclusive",
    ),
    "gpu_count_per_node": (
        "GPU count per node partition queue",
        "GPUs per compute node specifications",
        "each GPU node has GPUs",
    ),
    "gpu_models": (
        "GPU model partition queue specifications",
        "NVIDIA AMD Intel GPU node type",
    ),
    "features": (
        "partition node features architecture",
        "queue node type hardware features",
    ),
    "manager_worker_connectivity": (
        "manager worker connectivity login compute TCP",
        "compute node connect to login manager",
    ),
    "worker_worker_connectivity": (
        "worker compute node to node connectivity TCP",
        "inter-node application network policy",
    ),
    "outbound_compute": (
        "outbound compute node internet network egress",
        "compute nodes access external network",
    ),
    "charging_model": (
        "charging accounting service units credits allocation cost",
        "job charge cores memory node allocation",
    ),
    "charging_unit": (
        "charging unit service unit node hour core hour",
        "allocation accounting unit",
    ),
    "filesystem_storage_charged": (
        "filesystem storage charged allocation quota cost",
        "storage usage accounting charge",
    ),
    "storage_compute_visible": (
        "storage filesystem visible compute nodes",
        "home scratch work mounted compute nodes",
    ),
    "storage_compute_readable": (
        "storage filesystem readable compute nodes",
        "compute jobs read home scratch work",
    ),
    "storage_compute_writable": (
        "storage filesystem writable compute nodes",
        "compute jobs write home scratch work",
    ),
    "storage_shared_across_compute_nodes": (
        "shared filesystem across compute nodes",
        "same storage mounted all nodes",
        "mounts three file systems shared across all nodes home work scratch",
    ),
    "storage_backup_policy": (
        "storage filesystem backup policy",
        "home scratch work backed up",
    ),
    "purge_after_days": (
        "purge retention deletion expiration scratch policy",
        "scratch files removed after days",
    ),
    "storage_purge_condition": (
        "storage purge condition inactive files",
        "scratch retention deletion condition",
    ),
}

_SCHEDULER_FIELD_QUERIES: dict[str, dict[str, tuple[str, ...]]] = {
    "slurm": {
        "allocation_required": (
            "Slurm allocation account project required job submission",
            "job submission requires allocation account",
        ),
        "required_submission_options": (
            "required sbatch submission options account partition",
            "job script account partition time nodes memory",
            "mandatory SBATCH fields",
            "must at minimum specify",
            "account -A --account allocation account",
            "partition -p showpartitions",
            "site-specific mandatory SBATCH directives licenses constraint reservation",
            "additional required #SBATCH options",
            "common sbatch options unsupported not available avoid",
            "gres gpus-per-task mem export array dependency mail-type",
        ),
    },
    "htcondor": {
        "allocation_required": (
            "HTCondor submit access permission project accounting group",
            "Condor pool authorization requirements",
        ),
        "required_submission_options": (
            "HTCondor submit file condor_submit required attributes",
            "request_cpus request_memory request_gpus",
            "multicore SMP request_cpus GPU request_gpus",
            "universe executable should_transfer_files when_to_transfer_output",
            "Condor submit description queue",
            "site-specific mandatory HTCondor submit attributes",
            "required ClassAd accounting_group requirements +ProjectName",
        ),
        "submission_host": (
            "HTCondor login host condor_submit submit jobs",
            "where to log in to submit Condor jobs",
        ),
        "machine_requirements_supported": (
            "HTCondor specify machine cluster memory processor requirements",
            "Condor machine selection requirements",
        ),
        "dynamic_slots_enabled": (
            "HTCondor pool configured dynamic slots multicore SMP",
            "dynamic slots request_cpus",
        ),
        "bulk_submission_supported": (
            "HTCondor submit many jobs queue PROCESS macro",
            "Condor bulk submission queue several jobs",
        ),
        "completion_email_supported": (
            "HTCondor completion email disabled site policy",
            "job complete email notification",
        ),
    },
}

_RESOURCE_QUERY = {
    "maximum_walltime_seconds": "partition queue maximum walltime",
    "maximum_nodes_per_job": "partition queue maximum nodes per job",
    "shared_nodes": "partition queue node sharing",
    "gpu_count_per_node": "partition queue GPUs per node",
    "gpu_models": "partition queue GPU model",
    "features": "partition queue node features",
    "storage_compute_visible": "storage filesystem compute visibility",
    "storage_compute_readable": "storage filesystem compute readable",
    "storage_compute_writable": "storage filesystem compute writable",
    "storage_shared_across_compute_nodes": "shared storage all compute nodes",
    "storage_backup_policy": "storage filesystem backup policy",
    "purge_after_days": "storage scratch purge retention days",
    "storage_purge_condition": "storage scratch purge condition",
}

FULL_CORPUS_BATCH_CHARS = 12_000


def select_context(
    chunks: list[CorpusChunk],
    *,
    scheduler: str,
    group: ExtractionGroupName,
    fields: tuple[str, ...],
    mode: ContextMode,
    resources_by_field: dict[str, set[str]] | None = None,
    expanded_queries_by_field: dict[str, list[str]] | None = None,
    maximum_chunks_per_field: int = 8,
    maximum_chunks: int = 32,
    maximum_chars: int = 48_000,
    expanded_chunks_per_field: int = 6,
    expanded_maximum_chunks: int = 40,
    expanded_maximum_chars: int = 60_000,
) -> ContextSelection:
    """Retrieve target-site chunks independently for each requested field."""

    target_chunks = [chunk for chunk in chunks if chunk.scope == "target_site"]
    eligible = _deduplicate_content(target_chunks)
    resources = resources_by_field or {}
    expanded_queries = expanded_queries_by_field or {}

    if mode == "full-corpus":
        selected = eligible
        retrievals = [
            FieldRetrieval(
                field=field,
                queries=[],
                hits=[
                    RetrievalHit(chunk_id=chunk.chunk_id, score=None)
                    for chunk in selected
                ],
            )
            for field in fields
        ]
    else:
        field_limit = (
            expanded_chunks_per_field
            if mode == "llm-expanded-bm25"
            else maximum_chunks_per_field
        )
        ranked = {
            field: _retrieve_field(
                eligible,
                field,
                scheduler,
                mode,
                resources.get(field, set()),
                expanded_queries.get(field, []),
                field_limit,
            )
            for field in fields
        }
        group_maximum_chunks = (
            expanded_maximum_chunks
            if mode == "llm-expanded-bm25"
            else maximum_chunks
        )
        group_maximum_chars = (
            expanded_maximum_chars
            if mode == "llm-expanded-bm25"
            else maximum_chars
        )
        selected, included = _merge_field_results(
            fields,
            ranked,
            group_maximum_chunks,
            group_maximum_chars,
        )
        retrievals = [
            FieldRetrieval(
                field=field,
                queries=_queries(
                    field,
                    scheduler,
                    mode,
                    resources.get(field, set()),
                    expanded_queries.get(field, []),
                ),
                hits=[
                    RetrievalHit(chunk_id=chunk.chunk_id, score=round(score, 6))
                    for score, chunk in included[field]
                ],
            )
            for field in fields
        ]

    return ContextSelection(
        group=group,
        mode=mode,
        chunks=selected,
        selected_chunk_ids=[chunk.chunk_id for chunk in selected],
        retrievals=retrievals,
    )


def batch_full_corpus(
    selection: ContextSelection,
    *,
    maximum_chars: int = FULL_CORPUS_BATCH_CHARS,
) -> list[ContextSelection]:
    """Split full-corpus context into ordered batches without splitting chunks."""

    if selection.mode != "full-corpus":
        raise ValueError("Only full-corpus context can be batched.")
    if maximum_chars < 1:
        raise ValueError("maximum_chars must be positive.")

    chunk_batches: list[list[CorpusChunk]] = []
    current: list[CorpusChunk] = []
    current_chars = 0
    for chunk in selection.chunks:
        chunk_chars = _context_chars(chunk)
        if current and current_chars + chunk_chars > maximum_chars:
            chunk_batches.append(current)
            current = []
            current_chars = 0
        current.append(chunk)
        current_chars += chunk_chars
    if current:
        chunk_batches.append(current)

    return [_selection_batch(selection, chunks) for chunks in chunk_batches]


def _retrieve_field(
    chunks: list[CorpusChunk],
    field: str,
    scheduler: str,
    mode: ContextMode,
    resources: set[str],
    expanded_queries: list[str],
    limit: int,
) -> list[tuple[float, CorpusChunk]]:
    queries = _queries(field, scheduler, mode, resources, expanded_queries)
    variant_scores = [_bm25(query, chunks) for query in queries]
    rankings = [
        sorted(
            (
                (score, index)
                for index, score in enumerate(scores)
                if score > 0
            ),
            key=lambda item: (-item[0], chunks[item[1]].chunk_id),
        )
        for scores in variant_scores
    ]
    ranked: list[tuple[float, CorpusChunk]] = []
    selected: set[int] = set()
    positions = [0 for _ in rankings]
    while any(
        position < len(items)
        for position, items in zip(positions, rankings, strict=True)
    ):
        for ranking_index, items in enumerate(rankings):
            while (
                positions[ranking_index] < len(items)
                and items[positions[ranking_index]][1] in selected
            ):
                positions[ranking_index] += 1
            if positions[ranking_index] >= len(items):
                continue
            score, chunk_index = items[positions[ranking_index]]
            positions[ranking_index] += 1
            selected.add(chunk_index)
            ranked.append((score, chunks[chunk_index]))
            if len(ranked) == limit:
                return ranked
    return ranked


def base_queries(field: str, scheduler: str, resources: set[str]) -> list[str]:
    """Return reviewed BM25 query variants for one profile field."""

    queries = list(_COMMON_FIELD_QUERIES.get(field, ()))
    queries.extend(_SCHEDULER_FIELD_QUERIES.get(scheduler, {}).get(field, ()))
    if resources and field in _RESOURCE_QUERY:
        queries.append(f"{' '.join(sorted(resources))} {_RESOURCE_QUERY[field]}")
    return list(dict.fromkeys(queries))


def _queries(
    field: str,
    scheduler: str,
    mode: ContextMode,
    resources: set[str],
    expanded_queries: list[str],
) -> list[str]:
    reviewed = base_queries(field, scheduler, resources)
    if mode != "llm-expanded-bm25":
        return reviewed
    return list(dict.fromkeys([*reviewed, *expanded_queries]))


def _merge_field_results(
    fields: tuple[str, ...],
    ranked: dict[str, list[tuple[float, CorpusChunk]]],
    maximum_chunks: int,
    maximum_chars: int,
) -> tuple[list[CorpusChunk], dict[str, list[tuple[float, CorpusChunk]]]]:
    selected: list[CorpusChunk] = []
    selected_ids: set[str] = set()
    included: dict[str, list[tuple[float, CorpusChunk]]] = {
        field: [] for field in fields
    }
    characters = 0
    depth = max((len(ranked[field]) for field in fields), default=0)

    for index in range(depth):
        for field in fields:
            if index >= len(ranked[field]):
                continue
            score, chunk = ranked[field][index]
            if chunk.chunk_id in selected_ids:
                included[field].append((score, chunk))
                continue
            if len(selected) >= maximum_chunks or characters + len(chunk.text) > maximum_chars:
                continue
            selected.append(chunk)
            selected_ids.add(chunk.chunk_id)
            included[field].append((score, chunk))
            characters += len(chunk.text)
    return selected, included


def _deduplicate_content(chunks: list[CorpusChunk]) -> list[CorpusChunk]:
    result: list[CorpusChunk] = []
    seen: set[str] = set()
    for chunk in chunks:
        if chunk.content_hash not in seen:
            seen.add(chunk.content_hash)
            result.append(chunk)
    return result


def _context_chars(chunk: CorpusChunk) -> int:
    """Approximate prompt size while retaining each heading-aware chunk whole."""

    return (
        len(chunk.text)
        + len(chunk.source_url)
        + len(chunk.title)
        + sum(len(heading) for heading in chunk.heading_path)
        + 100
    )


def _selection_batch(
    selection: ContextSelection,
    chunks: list[CorpusChunk],
) -> ContextSelection:
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    return selection.model_copy(
        update={
            "chunks": chunks,
            "selected_chunk_ids": [chunk.chunk_id for chunk in chunks],
            "retrievals": [
                retrieval.model_copy(
                    update={
                        "hits": [
                            hit for hit in retrieval.hits if hit.chunk_id in chunk_ids
                        ]
                    }
                )
                for retrieval in selection.retrievals
            ],
        }
    )


def _bm25(query: str, chunks: list[CorpusChunk]) -> list[float]:
    if not chunks:
        return []
    documents = [
        _tokens(f"{chunk.title} {' '.join(chunk.heading_path)} {chunk.text}")
        for chunk in chunks
    ]
    query_terms = set(_tokens(query))
    average_length = sum(len(document) for document in documents) / len(documents)
    frequencies = {
        term: sum(term in set(document) for document in documents) for term in query_terms
    }
    scores: list[float] = []
    for chunk, document in zip(chunks, documents, strict=True):
        counts = Counter(document)
        score = 0.0
        table_presence_bonus = 0.0
        for term in query_terms:
            count = counts[term]
            if count == 0:
                continue
            inverse = math.log(
                1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            denominator = count + 1.5 * (1 - 0.75 + 0.75 * len(document) / average_length)
            score += inverse * count * 2.5 / denominator
            if chunk.block_kind == "table":
                table_presence_bonus += inverse
        score += 1.5 * table_presence_bonus
        scores.append(score)
    return scores


def _tokens(value: str) -> list[str]:
    return re.findall(r"--?[a-z][a-z0-9-]*|[a-z0-9]+", value.lower())
