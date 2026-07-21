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
    NetworkExtractionResult,
    NetworkFinding,
    OperationalExtractionResult,
    PartitionFinding,
    RuntimeMode,
    StoragePolicyFinding,
    SubmissionExtractionResult,
    SubmissionOptionFinding,
)
from hpc_site_preflight.documentation.retrieval import select_context
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
_ExtractionResult: TypeAlias = (
    SubmissionExtractionResult | NetworkExtractionResult | OperationalExtractionResult
)
_RESULT_TYPES: dict[ExtractionGroupName, type[BaseModel]] = {
    "submission": SubmissionExtractionResult,
    "network": NetworkExtractionResult,
    "operational": OperationalExtractionResult,
}


@dataclass(frozen=True)
class _ValidatedGroup:
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
    """Extract three independent policy groups and preserve partial success."""

    findings: list[DocumentationFinding] = []
    rejected: list[str] = []
    selected_chunk_ids: list[str] = []
    retrievals: list[FieldRetrieval] = []
    resources_by_field = {
        "maximum_walltime_seconds": partition_names,
        "purge_after_days": storage_names,
    }

    for group in _GROUPS:
        requested_fields = _requested_fields(group, scheduler, storage_names)
        with tracker.stage("documentation_context_selection", display=False):
            selection = select_context(
                chunks,
                group=group,
                fields=requested_fields,
                mode=context_mode,
                resources_by_field=resources_by_field,
            )
            spans = build_evidence_spans(selection)
            prompt = build_extraction_prompt(site_name, group, selection, spans)
            tracker.progress(
                f"{group} retrieval selected {len(selection.chunks)} unique chunk(s) "
                f"for {len(selection.retrievals)} field(s)"
            )
        selected_chunk_ids.extend(selection.selected_chunk_ids)
        retrievals.extend(selection.retrievals)

        result_type = _RESULT_TYPES[group]
        tracker.progress(f"Requesting {group} policy findings")
        try:
            response = provider.generate_structured(
                StructuredModelRequest(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    output_name=f"extract_{group}",
                    output_description=f"Submit documented {group} policy findings.",
                ),
                result_type,
                tracker,
            )
            result = cast(_ExtractionResult, response.parse_as(result_type))
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
                corrected_response = provider.generate_structured(
                    StructuredModelRequest(
                        system_prompt=_SYSTEM_PROMPT,
                        user_prompt=correction_prompt,
                        output_name=f"extract_{group}",
                        output_description=f"Correct invalid {group} findings once.",
                    ),
                    result_type,
                    tracker,
                )
                corrected_result = cast(
                    _ExtractionResult,
                    corrected_response.parse_as(result_type),
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

    accepted_findings = _deduplicate_findings(findings)
    found_fields = {_retrieval_field(finding) for finding in accepted_findings}
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
    """Return an explicit partial result when documentation inputs are unavailable."""

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
    """Assign stable IDs to exact sentences, paragraphs, or table rows."""

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
    group: ExtractionGroupName,
    selection: ContextSelection,
    spans: list[EvidenceSpan],
) -> str:
    """Render one compact field-local span library."""

    lines = [
        f"SITE: {site_name}",
        f"GROUP: {group}",
        "RETRIEVAL TARGETS: "
        + ", ".join(retrieval.field for retrieval in selection.retrievals),
        "FIELD RETRIEVAL:",
    ]
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


def _validate_group(
    result: _ExtractionResult,
    spans: list[EvidenceSpan],
    retrievals: list[FieldRetrieval],
    scheduler: str,
    partition_names: set[str],
    storage_names: set[str],
) -> _ValidatedGroup:
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
    return [line.strip() for line in text.splitlines() if line.strip()]


def _text_spans(text: str) -> list[str]:
    paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
    spans: list[str] = []
    for paragraph in paragraphs:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", paragraph)]
        spans.extend(item for item in sentences if item)
    return spans


def _finding_key(finding: DocumentationFinding) -> tuple[str, str | None]:
    if isinstance(finding, AllocationRequiredFinding):
        return "allocation_required", None
    if isinstance(finding, SubmissionOptionFinding):
        return "required_submission_options", finding.name
    if isinstance(finding, PartitionFinding):
        return "maximum_walltime_seconds", finding.name
    if isinstance(finding, NetworkFinding):
        return _NETWORK_RETRIEVAL[finding.name], None
    if isinstance(finding, ChargingModelFinding):
        return "charging_model", None
    return "purge_after_days", finding.name


def _retrieval_field(finding: DocumentationFinding) -> str:
    return _finding_key(finding)[0]


def _deduplicate_findings(findings: list[DocumentationFinding]) -> list[DocumentationFinding]:
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
    fields = list(_GROUP_FIELDS[group])
    if scheduler != "slurm" and "maximum_walltime_seconds" in fields:
        fields.remove("maximum_walltime_seconds")
    if not storage_names and "purge_after_days" in fields:
        fields.remove("purge_after_days")
    return tuple(fields)
