"""Deterministic full-corpus and BM25 context selection."""

import math
import re
from collections import Counter

from hpc_site_preflight.documentation.models import (
    ContextMode,
    ContextSelection,
    CorpusChunk,
    ExtractionGroupName,
)

_QUERIES = {
    "submission": "submit job account allocation option queue partition walltime",
    "network": "manager worker compute node network outbound port connectivity",
    "operational": "charging accounting scratch storage purge retention policy",
}
_EXPANSIONS = {
    "submission": "sbatch condor_submit #SBATCH project maximum duration required",
    "network": "tcp firewall egress internet inter-node login address",
    "operational": "service units credits deletion expiration filesystem cost",
}


def select_context(
    chunks: list[CorpusChunk],
    *,
    group: ExtractionGroupName,
    mode: ContextMode,
    maximum_chunks: int = 8,
    maximum_chars: int = 12_000,
) -> ContextSelection:
    """Select target-site chunks with one reproducible strategy."""

    target_chunks = sorted(
        (chunk for chunk in chunks if chunk.scope == "target_site"),
        key=lambda chunk: chunk.chunk_id,
    )
    query = _QUERIES[group]
    if mode == "full-corpus":
        ranked = target_chunks
    else:
        if mode == "schema-expanded-bm25":
            query = f"{query} {_EXPANSIONS[group]}"
        scores = _bm25(query, target_chunks)
        ranked_pairs = sorted(
            zip(scores, target_chunks, strict=True),
            key=lambda item: (-item[0], item[1].chunk_id),
        )
        ranked = [chunk for score, chunk in ranked_pairs if score > 0]

    selected: list[CorpusChunk] = []
    characters = 0
    for chunk in ranked:
        if len(selected) >= maximum_chunks or characters + len(chunk.text) > maximum_chars:
            break
        selected.append(chunk)
        characters += len(chunk.text)
    return ContextSelection(
        group=group,
        mode=mode,
        query=query,
        chunks=selected,
        selected_chunk_ids=[chunk.chunk_id for chunk in selected],
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
