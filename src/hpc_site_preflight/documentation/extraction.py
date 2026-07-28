"""Context selection, typed extraction, and local evidence validation."""

import re
from dataclasses import dataclass
from typing import TypeAlias, cast

from pydantic import BaseModel

from hpc_site_preflight.documentation.models import (
    AllocationRequiredFinding,
    ChargingModelFinding,
    ContextMode,
    ContextSelection,
    CorpusChunk,
    DocumentationCitation,
    DocumentationEvidence,
    DocumentationFinding,
    EvidenceSpan,
    ExtractionGroupName,
    FieldRetrieval,
    FullCorpusExtractionResult,
    NetworkExtractionResult,
    NetworkFinding,
    OperationalExtractionResult,
    PartitionFinding,
    RuntimeMode,
    StoragePolicyFinding,
    SubmissionExtractionResult,
    SubmissionOptionFinding,
    SubmissionOptionName,
    UnmappedSubmissionOptionFinding,
)
from hpc_site_preflight.documentation.query_expansion import expand_queries
from hpc_site_preflight.documentation.retrieval import batch_full_corpus, select_context
from hpc_site_preflight.exceptions import ModelProviderError
from hpc_site_preflight.providers.base import (
    ModelProvider,
    ModelProviderName,
    StructuredModelRequest,
)
from hpc_site_preflight.reporting.tracker import RunTracker

_SYSTEM_PROMPT = """Extract only documented HPC site policy from the supplied exact spans.
Return values using the provided typed schema and canonical resource names.
Every returned value requires at least one supporting span ID.
Do not infer connectivity from network architecture.
Do not invent port ranges, limits, charging rules, or purge periods.
Use null or an empty list when the documentation does not state a value."""

_GROUP_FIELDS = {
    "submission": (
        "allocation_required",
        "required_submission_options",
        "maximum_walltime_seconds",
    ),
    "network": (
        "manager_worker_connectivity",
        "worker_worker_connectivity",
        "outbound_compute",
    ),
    "operational": ("charging_model", "purge_after_days"),
}
_GROUPS: tuple[ExtractionGroupName, ...] = ("submission", "network", "operational")
_NETWORK_RETRIEVAL = {
    "manager_worker": "manager_worker_connectivity",
    "worker_worker": "worker_worker_connectivity",
    "outbound_compute": "outbound_compute",
}
_SLURM_OPTIONS = {"account", "partition", "nodes", "cpus-per-task", "time"}
_HTCONDOR_OPTIONS = {"request_cpus", "request_memory", "request_gpus"}
_OPTION_FLAGS = {
    "-A": "account",
    "--account": "account",
    "-p": "partition",
    "--partition": "partition",
    "-N": "nodes",
    "--nodes": "nodes",
    "--cpus-per-task": "cpus-per-task",
    "-t": "time",
    "--time": "time",
}
_ExtractionResult: TypeAlias = (
    SubmissionExtractionResult | NetworkExtractionResult | OperationalExtractionResult
)
_RESULT_TYPES: dict[ExtractionGroupName, type[BaseModel]] = {
    "submission": SubmissionExtractionResult,
    "network": NetworkExtractionResult,
    "operational": OperationalExtractionResult,
}


def _submission_option_instructions(scheduler: str) -> list[str]:
    """Return the scheduler-specific canonical and unmapped option instructions."""

    options = _SLURM_OPTIONS if scheduler == "slurm" else _HTCONDOR_OPTIONS
    examples = (
        'Map "Account (-A or --account)" to name "account" and '
        '"Partition (-p)" to name "partition".'
        if scheduler == "slurm"
        else 'Map "request_cpus" to canonical name "request_cpus".'
    )
    return [
        "CANONICAL SUBMISSION OPTIONS: " + ", ".join(sorted(options)),
        "Put recognized requirements in submission_options using only those exact names.",
        examples,
        "Put every explicitly documented requirement that does not map to those names in "
        "unmapped_options with its exact documented name and syntax. Do not discard it.",
    ]


def _canonical_option_name(
    documented_name: str,
    documented_syntax: list[str],
    allowed_options: set[str],
) -> SubmissionOptionName | None:
    """Map only reviewed canonical names and exact scheduler flags."""

    normalized_name = documented_name.strip().casefold()
    by_name = {name.casefold(): name for name in allowed_options}
    if normalized_name in by_name:
        return cast(SubmissionOptionName, by_name[normalized_name])

    text = " ".join([documented_name, *documented_syntax])
    for flag in re.findall(r"(?<![A-Za-z0-9_-])--?[A-Za-z][A-Za-z0-9-]*", text):
        name = _OPTION_FLAGS.get(flag)
        if name in allowed_options:
            return cast(SubmissionOptionName, name)
    return None


@dataclass(frozen=True)
class _ValidatedGroup:
    """Hold accepted documentation findings and rejection messages for one policy group."""

    findings: list[DocumentationFinding]
    rejected: list[str]


def extract_documentation(
    *,
    site_id: str,
    site_name: str,
    scheduler: str,
    partition_names: set[str],
    storage_names: set[str],
    chunks: list[CorpusChunk],
    context_mode: ContextMode,
    model_mode: RuntimeMode,
    model_provider: ModelProviderName,
    model: str | None,
    web_mode: RuntimeMode,
    provider: ModelProvider,
    tracker: RunTracker,
) -> DocumentationEvidence:
    """Extract evidence-backed policy findings from normalized documentation chunks.

    Site resources constrain valid names while the context mode controls retrieval. Submission,
    network, and operational groups are retrieved and modeled independently, validated locally,
    and combined into accepted, rejected, and unresolved documentation evidence.
    """

    resources_by_field = {
        "maximum_walltime_seconds": partition_names,
        "purge_after_days": storage_names,
    }
    requested_query_fields = tuple(
        dict.fromkeys(
            field
            for group in _GROUPS
            for field in _requested_fields(group, scheduler, storage_names)
        )
    )
    expanded_queries: dict[str, list[str]] = {}
    expansion_error: str | None = None
    if context_mode == "llm-expanded-bm25":
        expanded_queries, expansion_error = expand_queries(
            site_name=site_name,
            scheduler=scheduler,
            fields=requested_query_fields,
            resources_by_field=resources_by_field,
            provider=provider,
            tracker=tracker,
        )

    findings: list[DocumentationFinding] = []
    rejected: list[str] = [expansion_error] if expansion_error else []
    selected_chunk_ids: list[str] = []
    retrievals: list[FieldRetrieval] = []

    groups: tuple[ExtractionGroupName, ...] = (
        () if context_mode == "full-corpus" else _GROUPS
    )
    for group in groups:
        requested_fields = _requested_fields(group, scheduler, storage_names)
        with tracker.stage("documentation_context_selection", display=False):
            selection = select_context(
                chunks,
                group=group,
                fields=requested_fields,
                mode=context_mode,
                resources_by_field=resources_by_field,
                expanded_queries_by_field=expanded_queries,
            )
            spans = build_evidence_spans(selection)
            prompt = build_extraction_prompt(
                site_name,
                scheduler,
                group,
                selection,
                spans,
            )
            tracker.progress(
                f"{group} retrieval selected {len(selection.chunks)} unique chunk(s) "
                f"for {len(selection.retrievals)} field(s)"
            )
        selected_chunk_ids.extend(selection.selected_chunk_ids)
        retrievals.extend(selection.retrievals)

        result_type = _RESULT_TYPES[group]
        tracker.progress(f"Requesting {group} policy findings")
        try:
            result = cast(
                _ExtractionResult,
                provider.generate_structured(
                    StructuredModelRequest(
                        system_prompt=_SYSTEM_PROMPT,
                        user_prompt=prompt,
                        output_name=f"extract_{group}",
                        output_description=f"Submit documented {group} policy findings.",
                    ),
                    result_type,
                    tracker,
                ),
            )
        except ModelProviderError as exc:
            rejected.append(f"{group}: model request failed: {exc}")
            continue

        with tracker.stage("documentation_evidence_validation", display=False):
            validated = _validate_group(
                result,
                spans,
                selection.retrievals,
                scheduler,
                partition_names,
                storage_names,
            )
        findings.extend(validated.findings)
        rejected.extend(validated.rejected)
        tracker.progress(
            f"Validated {group}: {len(validated.findings)} accepted, "
            f"{len(validated.rejected)} rejected"
        )

        if validated.rejected:
            tracker.progress(f"Requesting one correction for {group} policy findings")
            correction_prompt = (
                prompt
                + "\n\nCORRECTION: Return only corrected values for these local errors. "
                "Use null or empty lists for all other schema fields:\n- "
                + "\n- ".join(validated.rejected)
            )
            try:
                corrected_result = cast(
                    _ExtractionResult,
                    provider.generate_structured(
                        StructuredModelRequest(
                            system_prompt=_SYSTEM_PROMPT,
                            user_prompt=correction_prompt,
                            output_name=f"extract_{group}",
                            output_description=f"Correct invalid {group} findings once.",
                        ),
                        result_type,
                        tracker,
                    ),
                )
            except ModelProviderError as exc:
                rejected.append(f"{group}: correction failed: {exc}")
                continue
            with tracker.stage("documentation_evidence_validation", display=False):
                corrected = _validate_group(
                    corrected_result,
                    spans,
                    selection.retrievals,
                    scheduler,
                    partition_names,
                    storage_names,
                )
            findings.extend(corrected.findings)
            rejected.extend(f"after correction: {item}" for item in corrected.rejected)

    if context_mode == "full-corpus":
        full_findings, full_rejected, full_chunk_ids, full_retrievals = (
            _extract_full_corpus_batches(
                site_name=site_name,
                scheduler=scheduler,
                partition_names=partition_names,
                storage_names=storage_names,
                chunks=chunks,
                resources_by_field=resources_by_field,
                provider=provider,
                tracker=tracker,
            )
        )
        findings.extend(full_findings)
        rejected.extend(full_rejected)
        selected_chunk_ids.extend(full_chunk_ids)
        retrievals.extend(full_retrievals)

    accepted_findings = _deduplicate_findings(findings)
    found_fields = {
        _retrieval_field(finding)
        for finding in accepted_findings
        if not isinstance(finding, UnmappedSubmissionOptionFinding)
    }
    unresolved = sorted(_expected_fields(scheduler, storage_names) - found_fields)
    return DocumentationEvidence(
        site_id=site_id,
        model_mode=model_mode,
        model_provider=model_provider,
        model=model,
        web_mode=web_mode,
        context_mode=context_mode,
        findings=accepted_findings,
        rejected=rejected,
        unresolved=unresolved,
        selected_chunk_ids=list(dict.fromkeys(selected_chunk_ids)),
        retrieval=_mark_cited(retrievals, accepted_findings),
    )


def _extract_full_corpus_batches(
    *,
    site_name: str,
    scheduler: str,
    partition_names: set[str],
    storage_names: set[str],
    chunks: list[CorpusChunk],
    resources_by_field: dict[str, set[str]],
    provider: ModelProvider,
    tracker: RunTracker,
) -> tuple[
    list[DocumentationFinding],
    list[str],
    list[str],
    list[FieldRetrieval],
]:
    """Extract every target-site chunk through bounded combined-schema calls."""

    with tracker.stage("documentation_context_selection", display=False):
        selections = {
            group: select_context(
                chunks,
                group=group,
                fields=_requested_fields(group, scheduler, storage_names),
                mode="full-corpus",
                resources_by_field=resources_by_field,
            )
            for group in _GROUPS
        }
        batches = {
            group: batch_full_corpus(selection)
            for group, selection in selections.items()
        }

    batch_count = len(batches["submission"])
    selected_chunk_ids = selections["submission"].selected_chunk_ids
    retrievals = [
        retrieval
        for group in _GROUPS
        for retrieval in selections[group].retrievals
    ]
    tracker.progress(
        f"Full corpus selected {len(selected_chunk_ids)} unique chunk(s) "
        f"in {batch_count} bounded batch(es)"
    )

    findings: list[DocumentationFinding] = []
    rejected: list[str] = []
    for index in range(batch_count):
        batch_number = index + 1
        batch_selections = {group: batches[group][index] for group in _GROUPS}
        spans = build_evidence_spans(batch_selections["submission"])
        prompt = build_full_corpus_prompt(
            site_name,
            scheduler,
            batch_number,
            batch_count,
            batch_selections,
            spans,
        )
        tracker.progress(
            f"Requesting full-corpus batch {batch_number}/{batch_count} "
            f"({len(batch_selections['submission'].chunks)} chunk(s))"
        )
        try:
            result = provider.generate_structured(
                StructuredModelRequest(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    output_name="extract_full_corpus",
                    output_description=(
                        "Submit documented policy findings from this corpus batch."
                    ),
                ),
                FullCorpusExtractionResult,
                tracker,
            )
        except ModelProviderError as exc:
            rejected.append(
                f"full-corpus batch {batch_number}/{batch_count}: "
                f"model request failed: {exc}"
            )
            continue

        validated = _validate_full_corpus_result(
            result,
            batch_selections,
            spans,
            scheduler,
            partition_names,
            storage_names,
            tracker,
        )
        findings.extend(validated.findings)
        rejected.extend(
            f"batch {batch_number}/{batch_count}: {item}"
            for item in validated.rejected
        )
        tracker.progress(
            f"Validated full-corpus batch {batch_number}/{batch_count}: "
            f"{len(validated.findings)} accepted, "
            f"{len(validated.rejected)} rejected"
        )

        if not validated.rejected:
            continue
        tracker.progress(
            f"Requesting one correction for full-corpus batch "
            f"{batch_number}/{batch_count}"
        )
        correction_prompt = (
            prompt
            + "\n\nCORRECTION: Return only corrected values for these local errors. "
            "Use null or empty lists for all other schema fields:\n- "
            + "\n- ".join(validated.rejected)
        )
        try:
            corrected_result = provider.generate_structured(
                StructuredModelRequest(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=correction_prompt,
                    output_name="extract_full_corpus",
                    output_description=(
                        "Correct invalid findings from this corpus batch once."
                    ),
                ),
                FullCorpusExtractionResult,
                tracker,
            )
        except ModelProviderError as exc:
            rejected.append(
                f"full-corpus batch {batch_number}/{batch_count}: "
                f"correction failed: {exc}"
            )
            continue
        corrected = _validate_full_corpus_result(
            corrected_result,
            batch_selections,
            spans,
            scheduler,
            partition_names,
            storage_names,
            tracker,
        )
        findings.extend(corrected.findings)
        rejected.extend(
            f"after correction batch {batch_number}/{batch_count}: {item}"
            for item in corrected.rejected
        )

    return findings, rejected, selected_chunk_ids, retrievals


def empty_documentation(
    *,
    site_id: str,
    context_mode: ContextMode,
    model_mode: RuntimeMode,
    model_provider: ModelProviderName,
    model: str | None,
    web_mode: RuntimeMode,
    scheduler: str,
    storage_names: set[str],
    reason: str,
) -> DocumentationEvidence:
    """Build documentation evidence with no findings when provider inputs are unavailable.

    Returns the failure reason as rejected evidence and lists every applicable policy field as
    unresolved, while preserving the requested runtime metadata.
    """

    return DocumentationEvidence(
        site_id=site_id,
        model_mode=model_mode,
        model_provider=model_provider,
        model=model,
        web_mode=web_mode,
        context_mode=context_mode,
        findings=[],
        rejected=[reason],
        unresolved=sorted(_expected_fields(scheduler, storage_names)),
        selected_chunk_ids=[],
        retrieval=[],
    )


def build_evidence_spans(selection: ContextSelection) -> list[EvidenceSpan]:
    """Split selected chunks into exact, citable text spans with stable IDs.

    Returns table rows or text sentences with their chunk, URL, heading, and scope metadata.
    """

    spans: list[EvidenceSpan] = []
    for chunk in selection.chunks:
        quotes = _table_rows(chunk.text) if chunk.block_kind == "table" else _text_spans(chunk.text)
        for index, quote in enumerate(quotes, start=1):
            spans.append(
                EvidenceSpan(
                    span_id=f"{chunk.chunk_id}:s{index}",
                    chunk_id=chunk.chunk_id,
                    source_url=chunk.source_url,
                    title=chunk.title,
                    heading=" > ".join(chunk.heading_path),
                    scope=chunk.scope,
                    quote=quote,
                )
            )
    return spans


def build_extraction_prompt(
    site_name: str,
    scheduler: str,
    group: ExtractionGroupName,
    selection: ContextSelection,
    spans: list[EvidenceSpan],
) -> str:
    """Build the model prompt for one site and policy group.

    Returns retrieval targets, field queries, selected chunk IDs, and exact citable spans as text;
    it does not include unselected corpus content.
    """

    lines = [
        f"SITE: {site_name}",
        f"GROUP: {group}",
        "RETRIEVAL TARGETS: "
        + ", ".join(retrieval.field for retrieval in selection.retrievals),
        "FIELD RETRIEVAL:",
    ]
    if group == "submission":
        lines.extend(_submission_option_instructions(scheduler))
    for retrieval in selection.retrievals:
        queries = " | ".join(retrieval.queries) or "full corpus; no ranking query"
        chunk_ids = ", ".join(hit.chunk_id for hit in retrieval.hits) or "none"
        lines.extend(
            [
                f"FIELD: {retrieval.field}",
                f"QUERIES: {queries}",
                f"RETRIEVED CHUNKS: {chunk_ids}",
            ]
        )
    lines.append("EXACT SPANS:")
    for span in spans:
        lines.extend(
            [
                f"[{span.span_id}]",
                f"URL: {span.source_url}",
                f"HEADING: {span.heading}",
                span.quote,
            ]
        )
    if not spans:
        lines.append("NO TARGET-SITE SPANS WERE SELECTED.")
    return "\n".join(lines)


def build_full_corpus_prompt(
    site_name: str,
    scheduler: str,
    batch_number: int,
    batch_count: int,
    selections: dict[ExtractionGroupName, ContextSelection],
    spans: list[EvidenceSpan],
) -> str:
    """Build one compact prompt covering all policy groups for a corpus batch."""

    chunks = selections["submission"].chunks
    lines = [
        f"SITE: {site_name}",
        f"FULL-CORPUS BATCH: {batch_number}/{batch_count}",
        "TARGET FIELDS:",
    ]
    lines.extend(_submission_option_instructions(scheduler))
    for group in _GROUPS:
        fields = ", ".join(item.field for item in selections[group].retrievals)
        lines.append(f"{group}: {fields}")
    lines.extend(
        [
            "BATCH CHUNKS: " + ", ".join(chunk.chunk_id for chunk in chunks),
            "EXACT SPANS:",
        ]
    )
    for span in spans:
        lines.extend(
            [
                f"[{span.span_id}]",
                f"URL: {span.source_url}",
                f"HEADING: {span.heading}",
                span.quote,
            ]
        )
    if not spans:
        lines.append("NO TARGET-SITE SPANS WERE SELECTED.")
    return "\n".join(lines)


def _validate_full_corpus_result(
    result: FullCorpusExtractionResult,
    selections: dict[ExtractionGroupName, ContextSelection],
    spans: list[EvidenceSpan],
    scheduler: str,
    partition_names: set[str],
    storage_names: set[str],
    tracker: RunTracker,
) -> _ValidatedGroup:
    """Validate the three typed groups returned for one full-corpus batch."""

    results: dict[ExtractionGroupName, _ExtractionResult] = {
        "submission": result.submission,
        "network": result.network,
        "operational": result.operational,
    }
    findings: list[DocumentationFinding] = []
    rejected: list[str] = []
    with tracker.stage("documentation_evidence_validation", display=False):
        for group in _GROUPS:
            validated = _validate_group(
                results[group],
                spans,
                selections[group].retrievals,
                scheduler,
                partition_names,
                storage_names,
            )
            findings.extend(validated.findings)
            rejected.extend(f"{group}: {item}" for item in validated.rejected)
    return _ValidatedGroup(findings=findings, rejected=rejected)


def _validate_group(
    result: _ExtractionResult,
    spans: list[EvidenceSpan],
    retrievals: list[FieldRetrieval],
    scheduler: str,
    partition_names: set[str],
    storage_names: set[str],
) -> _ValidatedGroup:
    """Validate one typed model proposal against retrieved evidence and measured resources.

    Returns accepted findings with full citations and rejection messages for invalid span IDs,
    unsupported scheduler options, or unmeasured partition and storage names.
    """

    span_map = {span.span_id: span for span in spans}
    retrieved_chunks = {
        retrieval.field: {hit.chunk_id for hit in retrieval.hits}
        for retrieval in retrievals
    }
    findings: list[DocumentationFinding] = []
    rejected: list[str] = []

    if isinstance(result, SubmissionExtractionResult):
        if result.allocation_required is not None:
            citations, error = _citations(
                "allocation_required",
                result.allocation_required.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(f"allocation_required: {error}")
            else:
                findings.append(
                    AllocationRequiredFinding(
                        allocation_required=result.allocation_required.value,
                        note=result.allocation_required.note,
                        citations=citations,
                    )
                )

        allowed_options = _SLURM_OPTIONS if scheduler == "slurm" else _HTCONDOR_OPTIONS
        for option in result.submission_options:
            if option.name not in allowed_options:
                rejected.append(
                    f"submission_options/{option.name}: option is not in the reviewed "
                    f"{scheduler} profile contract"
                )
                continue
            citations, error = _citations(
                "required_submission_options",
                option.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(f"submission_options/{option.name}: {error}")
            else:
                findings.append(
                    SubmissionOptionFinding(
                        name=option.name,
                        requirement=option.requirement,
                        note=option.note,
                        citations=citations,
                    )
                )

        for option in result.unmapped_options:
            citations, error = _citations(
                "required_submission_options",
                option.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(
                    f"unmapped_options/{option.documented_name}: {error}"
                )
                continue
            canonical_name = _canonical_option_name(
                option.documented_name,
                option.documented_syntax,
                allowed_options,
            )
            if canonical_name is not None:
                findings.append(
                    SubmissionOptionFinding(
                        name=canonical_name,
                        requirement=option.requirement,
                        note=option.note,
                        citations=citations,
                    )
                )
            else:
                findings.append(
                    UnmappedSubmissionOptionFinding(
                        documented_name=option.documented_name,
                        documented_syntax=option.documented_syntax,
                        requirement=option.requirement,
                        note=option.note,
                        citations=citations,
                    )
                )

        for partition in result.partitions:
            if scheduler != "slurm" or partition.name not in partition_names:
                rejected.append(
                    f"partitions/{partition.name}: partition is not present "
                    "in measured site resources"
                )
                continue
            citations, error = _citations(
                "maximum_walltime_seconds",
                partition.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(f"partitions/{partition.name}: {error}")
            else:
                findings.append(
                    PartitionFinding(
                        name=partition.name,
                        maximum_walltime_seconds=partition.maximum_walltime_seconds,
                        note=partition.note,
                        citations=citations,
                    )
                )

    elif isinstance(result, NetworkExtractionResult):
        for capability in result.network:
            retrieval_field = _NETWORK_RETRIEVAL[capability.name]
            citations, error = _citations(
                retrieval_field,
                capability.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(f"network/{capability.name}: {error}")
            else:
                findings.append(
                    NetworkFinding(
                        name=capability.name,
                        available=capability.available,
                        note=capability.note,
                        citations=citations,
                    )
                )

    elif isinstance(result, OperationalExtractionResult):
        if result.charging_model is not None:
            citations, error = _citations(
                "charging_model",
                result.charging_model.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(f"charging_model: {error}")
            else:
                findings.append(
                    ChargingModelFinding(
                        charging_model=result.charging_model.value,
                        note=result.charging_model.note,
                        citations=citations,
                    )
                )

        for storage in result.storage:
            if storage.name not in storage_names:
                rejected.append(
                    f"storage/{storage.name}: storage resource is not present "
                    "in measured site resources"
                )
                continue
            citations, error = _citations(
                "purge_after_days",
                storage.evidence_span_ids,
                span_map,
                retrieved_chunks,
            )
            if error:
                rejected.append(f"storage/{storage.name}: {error}")
            else:
                findings.append(
                    StoragePolicyFinding(
                        name=storage.name,
                        purge_after_days=storage.purge_after_days,
                        note=storage.note,
                        citations=citations,
                    )
                )

    return _ValidatedGroup(findings=findings, rejected=rejected)


def _citations(
    field: str,
    span_ids: list[str],
    spans: dict[str, EvidenceSpan],
    retrieved_chunks: dict[str, set[str]],
) -> tuple[list[DocumentationCitation], str | None]:
    """Resolve model-provided span IDs into citations for one requested field.

    Returns citations and no error when every span exists, came from that field's retrieval, and is
    target-site scoped; otherwise returns an empty list and a rejection reason.
    """

    if field not in retrieved_chunks:
        return [], "field was not requested for this site"
    unknown = [span_id for span_id in span_ids if span_id not in spans]
    if unknown:
        return [], "unknown evidence span " + ", ".join(unknown)
    wrong_field = [
        span_id
        for span_id in span_ids
        if spans[span_id].chunk_id not in retrieved_chunks[field]
    ]
    if wrong_field:
        return [], "evidence was not retrieved for this field: " + ", ".join(wrong_field)
    if any(spans[span_id].scope != "target_site" for span_id in span_ids):
        return [], "evidence is not target-site scoped"
    return [
        DocumentationCitation(
            span_id=spans[span_id].span_id,
            chunk_id=spans[span_id].chunk_id,
            url=spans[span_id].source_url,
            title=spans[span_id].title,
            heading=spans[span_id].heading,
            quote=spans[span_id].quote,
        )
        for span_id in span_ids
    ], None


def _table_rows(text: str) -> list[str]:
    """Return each nonempty table line as one exact evidence quote."""

    return [line.strip() for line in text.splitlines() if line.strip()]


def _text_spans(text: str) -> list[str]:
    """Return nonempty sentence-level quotes while preserving paragraph boundaries."""

    paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
    spans: list[str] = []
    for paragraph in paragraphs:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", paragraph)]
        spans.extend(item for item in sentences if item)
    return spans


def _finding_key(finding: DocumentationFinding) -> tuple[str, str | None]:
    """Return the canonical field and optional resource name that identify one finding."""

    if isinstance(finding, AllocationRequiredFinding):
        return "allocation_required", None
    if isinstance(finding, SubmissionOptionFinding):
        return "required_submission_options", finding.name
    if isinstance(finding, UnmappedSubmissionOptionFinding):
        return "required_submission_options", finding.documented_name
    if isinstance(finding, PartitionFinding):
        return "maximum_walltime_seconds", finding.name
    if isinstance(finding, NetworkFinding):
        return _NETWORK_RETRIEVAL[finding.name], None
    if isinstance(finding, ChargingModelFinding):
        return "charging_model", None
    return "purge_after_days", finding.name


def _retrieval_field(finding: DocumentationFinding) -> str:
    """Return the canonical retrieval field represented by an accepted finding."""

    return _finding_key(finding)[0]


def _deduplicate_findings(findings: list[DocumentationFinding]) -> list[DocumentationFinding]:
    """Keep the first accepted finding for each canonical field and resource pair."""

    result: list[DocumentationFinding] = []
    seen: set[tuple[str, str | None]] = set()
    for finding in findings:
        key = _finding_key(finding)
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def _mark_cited(
    retrievals: list[FieldRetrieval],
    findings: list[DocumentationFinding],
) -> list[FieldRetrieval]:
    """Return retrieval records with hits marked when an accepted finding cites their chunk."""

    cited = {
        (_retrieval_field(finding), citation.chunk_id)
        for finding in findings
        for citation in finding.citations
    }
    return [
        retrieval.model_copy(
            update={
                "hits": [
                    hit.model_copy(
                        update={"cited": (retrieval.field, hit.chunk_id) in cited}
                    )
                    for hit in retrieval.hits
                ]
            }
        )
        for retrieval in retrievals
    ]


def _expected_fields(scheduler: str, storage_names: set[str]) -> set[str]:
    """Return policy fields expected for the scheduler and observed storage resources."""

    fields = set().union(*_GROUP_FIELDS.values())
    if scheduler != "slurm":
        fields.discard("maximum_walltime_seconds")
    if not storage_names:
        fields.discard("purge_after_days")
    return fields


def _requested_fields(
    group: ExtractionGroupName,
    scheduler: str,
    storage_names: set[str],
) -> tuple[str, ...]:
    """Return fields from one extraction group that apply to the current site."""

    fields = list(_GROUP_FIELDS[group])
    if scheduler != "slurm" and "maximum_walltime_seconds" in fields:
        fields.remove("maximum_walltime_seconds")
    if not storage_names and "purge_after_days" in fields:
        fields.remove("purge_after_days")
    return tuple(fields)
