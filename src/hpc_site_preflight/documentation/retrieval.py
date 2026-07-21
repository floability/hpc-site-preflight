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

_FIELD_QUERIES: dict[str, tuple[str, ...]] = {
    "allocation_required": (
        "allocation account project required job submission",
        "job submission requires allocation account",
    ),
    "required_submission_options": (
        "required batch submission options account partition",
        "job script account partition time nodes memory",
    ),
    "maximum_walltime_seconds": (
        "partition maximum walltime time limit",
        "queue maximum job duration",
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
    "purge_after_days": (
        "purge retention deletion expiration scratch policy",
        "scratch files removed after days",
    ),
}

_FIELD_EXPANSIONS = {
    "allocation_required": "project account ACCESS allocation",
    "required_submission_options": "#SBATCH --account --partition --time --nodes",
    "maximum_walltime_seconds": "wall clock limit max time duration",
    "manager_worker_connectivity": "firewall socket head node login node",
    "worker_worker_connectivity": "interconnect peer socket firewall",
    "outbound_compute": "external internet egress firewall",
    "charging_model": "SU service unit credit billing consumption",
    "purge_after_days": "retention cleanup deletion inactive filesystem",
}

_RESOURCE_QUERY = {
    "maximum_walltime_seconds": "partition queue maximum walltime",
    "purge_after_days": "storage scratch purge retention days",
}


def select_context(
    chunks: list[CorpusChunk],
    *,
    group: ExtractionGroupName,
    fields: tuple[str, ...],
    mode: ContextMode,
    resources_by_field: dict[str, set[str]] | None = None,
    maximum_chunks_per_field: int = 4,
    maximum_chunks: int = 12,
    maximum_chars: int = 12_000,
) -> ContextSelection:
    """Retrieve target-site chunks independently for each requested field."""

    target_chunks = sorted(
        (chunk for chunk in chunks if chunk.scope == "target_site"),
        key=lambda chunk: chunk.chunk_id,
    )
    eligible = _deduplicate_content(target_chunks)
    resources = resources_by_field or {}

    if mode == "full-corpus":
        selected = _bounded(eligible, maximum_chunks, maximum_chars)
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
        ranked = {
            field: _retrieve_field(
                eligible,
                field,
                mode,
                resources.get(field, set()),
                maximum_chunks_per_field,
            )
            for field in fields
        }
        selected, included = _merge_field_results(
            fields,
            ranked,
            maximum_chunks,
            maximum_chars,
        )
        retrievals = [
            FieldRetrieval(
                field=field,
                queries=_queries(field, mode, resources.get(field, set())),
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


def _retrieve_field(
    chunks: list[CorpusChunk],
    field: str,
    mode: ContextMode,
    resources: set[str],
    limit: int,
) -> list[tuple[float, CorpusChunk]]:
    queries = _queries(field, mode, resources)
    variant_scores = [_bm25(query, chunks) for query in queries]
    ranked: list[tuple[float, CorpusChunk]] = []
    for index, chunk in enumerate(chunks):
        scores = [variant[index] for variant in variant_scores]
        score = max(scores) + 0.25 * sum(scores) if scores else 0.0
        if score > 0:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].chunk_id))
    return ranked[:limit]


def _queries(field: str, mode: ContextMode, resources: set[str]) -> list[str]:
    queries = list(_FIELD_QUERIES[field])
    if resources and field in _RESOURCE_QUERY:
        queries.append(f"{' '.join(sorted(resources))} {_RESOURCE_QUERY[field]}")
    if mode == "schema-expanded-bm25":
        expansion = _FIELD_EXPANSIONS[field]
        queries = [f"{query} {expansion}" for query in queries]
    return queries


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


def _bounded(
    chunks: list[CorpusChunk],
    maximum_chunks: int,
    maximum_chars: int,
) -> list[CorpusChunk]:
    selected: list[CorpusChunk] = []
    characters = 0
    for chunk in chunks:
        if len(selected) >= maximum_chunks:
            break
        if characters + len(chunk.text) > maximum_chars:
            continue
        selected.append(chunk)
        characters += len(chunk.text)
    return selected


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
    for document in documents:
        counts = Counter(document)
        score = 0.0
        for term in query_terms:
            count = counts[term]
            if count == 0:
                continue
            inverse = math.log(
                1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5)
            )
            denominator = count + 1.5 * (1 - 0.75 + 0.75 * len(document) / average_length)
            score += inverse * count * 2.5 / denominator
        scores.append(score)
    return scores


def _tokens(value: str) -> list[str]:
    return re.findall(r"--?[a-z][a-z0-9-]*|[a-z0-9]+", value.lower())
