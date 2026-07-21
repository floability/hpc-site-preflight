"""Context selection, exact spans, and locally validated extraction."""

import re
from dataclasses import dataclass

from hpc_site_preflight.documentation.models import (
    ContextMode,
    ContextSelection,
    CorpusChunk,
    DocumentationCitation,
    DocumentationEvidence,
    DocumentationFinding,
    EvidenceSpan,
    ExtractionCandidate,
    ExtractionGroupName,
    ExtractionResult,
    FieldRetrieval,
    RuntimeMode,
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
Use only allowed field names and span IDs.
Every non-null value requires at least one supporting span.
Do not infer connectivity from network architecture.
Do not invent port ranges, limits, charging rules, or purge periods.
Omit a finding when the documentation does not state it."""

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
_RESOURCE_FIELDS = {"maximum_walltime_seconds", "purge_after_days"}
_GROUPS: tuple[ExtractionGroupName, ...] = ("submission", "network", "operational")


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

        tracker.progress(f"Requesting {group} policy findings")
        try:
            response = provider.generate_structured(
                StructuredModelRequest(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=prompt,
                    output_name=f"extract_{group}",
                    output_description=f"Submit documented {group} policy findings.",
                ),
                ExtractionResult,
                tracker,
            )
        except ModelProviderError as exc:
            rejected.append(f"{group}: model request failed: {exc}")
            continue

        with tracker.stage("documentation_evidence_validation", display=False):
            validated = _validate_group(
                group,
                response.parse_as(ExtractionResult),
                spans,
                selection.retrievals,
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
                + "\n\nCORRECTION: Return only corrected findings for these local errors:\n- "
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
                    ExtractionResult,
                    tracker,
                )
            except ModelProviderError as exc:
                rejected.append(f"{group}: correction failed: {exc}")
                continue
            with tracker.stage("documentation_evidence_validation", display=False):
                corrected = _validate_group(
                    group,
                    corrected_response.parse_as(ExtractionResult),
                    spans,
                    selection.retrievals,
                    partition_names,
                    storage_names,
                )
            findings.extend(corrected.findings)
            rejected.extend(f"after correction: {item}" for item in corrected.rejected)

    found_fields = {finding.field for finding in findings}
    expected_fields = _expected_fields(scheduler, storage_names)
    unresolved = sorted(expected_fields - found_fields)
    accepted_findings = _deduplicate_findings(findings)
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
        "ALLOWED FIELDS: "
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
    group: ExtractionGroupName,
    result: ExtractionResult,
    spans: list[EvidenceSpan],
    retrievals: list[FieldRetrieval],
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
    for candidate in result.findings:
        error = _candidate_error(
            group,
            candidate,
            span_map,
            retrieved_chunks,
            partition_names,
            storage_names,
        )
        if error:
            rejected.append(f"{candidate.field}: {error}")
            continue
        citations = [
            DocumentationCitation(
                span_id=span_map[span_id].span_id,
                chunk_id=span_map[span_id].chunk_id,
                url=span_map[span_id].source_url,
                title=span_map[span_id].title,
                heading=span_map[span_id].heading,
                quote=span_map[span_id].quote,
            )
            for span_id in candidate.evidence_span_ids
        ]
        assert candidate.value is not None
        findings.append(
            DocumentationFinding(
                field=candidate.field,
                resource=candidate.resource,
                value=candidate.value,
                note=candidate.note,
                citations=citations,
            )
        )
    return _ValidatedGroup(findings=findings, rejected=rejected)


def _candidate_error(
    group: ExtractionGroupName,
    candidate: ExtractionCandidate,
    spans: dict[str, EvidenceSpan],
    retrieved_chunks: dict[str, set[str]],
    partition_names: set[str],
    storage_names: set[str],
) -> str | None:
    if candidate.field not in _GROUP_FIELDS[group]:
        return "field is outside this extraction group"
    if candidate.field not in retrieved_chunks:
        return "field was not requested for this site"
    if candidate.value is None:
        return "null values must be omitted rather than asserted"
    if candidate.field in _RESOURCE_FIELDS and not candidate.resource:
        return "resource name is required"
    if candidate.field == "maximum_walltime_seconds" and candidate.resource not in partition_names:
        return "partition is not present in measured site resources"
    if candidate.field == "purge_after_days" and candidate.resource not in storage_names:
        return "storage resource is not present in measured site resources"
    if candidate.field not in _RESOURCE_FIELDS and candidate.resource is not None:
        return "resource name is not allowed"
    if not candidate.evidence_span_ids:
        return "documented values require evidence"
    unknown = [span_id for span_id in candidate.evidence_span_ids if span_id not in spans]
    if unknown:
        return "unknown evidence span " + ", ".join(unknown)
    wrong_field = [
        span_id
        for span_id in candidate.evidence_span_ids
        if spans[span_id].chunk_id not in retrieved_chunks.get(candidate.field, set())
    ]
    if wrong_field:
        return "evidence was not retrieved for this field: " + ", ".join(wrong_field)
    if any(spans[span_id].scope != "target_site" for span_id in candidate.evidence_span_ids):
        return "evidence is not target-site scoped"
    return _value_error(candidate)


def _value_error(candidate: ExtractionCandidate) -> str | None:
    value = candidate.value
    boolean_fields = {
        "allocation_required",
        "manager_worker_connectivity",
        "worker_worker_connectivity",
        "outbound_compute",
    }
    if candidate.field in boolean_fields:
        return None if isinstance(value, bool) else "value must be boolean"
    if candidate.field in {"maximum_walltime_seconds", "purge_after_days"}:
        valid = isinstance(value, int) and not isinstance(value, bool) and value >= 0
        return None if valid else "value must be a non-negative integer"
    if candidate.field == "required_submission_options":
        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
        return None if valid else "value must be a list of option names"
    return None if isinstance(value, str) else "value must be a string"


def _table_rows(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _text_spans(text: str) -> list[str]:
    paragraphs = [item.strip() for item in text.split("\n\n") if item.strip()]
    spans: list[str] = []
    for paragraph in paragraphs:
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", paragraph)]
        spans.extend(item for item in sentences if item)
    return spans


def _deduplicate_findings(findings: list[DocumentationFinding]) -> list[DocumentationFinding]:
    result: list[DocumentationFinding] = []
    seen: set[tuple[str, str | None]] = set()
    for finding in findings:
        key = finding.field, finding.resource
        if key not in seen:
            seen.add(key)
            result.append(finding)
    return result


def _mark_cited(
    retrievals: list[FieldRetrieval],
    findings: list[DocumentationFinding],
) -> list[FieldRetrieval]:
    cited = {
        (finding.field, citation.chunk_id)
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
