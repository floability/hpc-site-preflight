"""Offline tests for the complete Phase D documentation pipeline."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from hpc_site_preflight.documentation.corpus import build_corpus
from hpc_site_preflight.documentation.discovery_agent import DiscoveryAgent
from hpc_site_preflight.documentation.extraction import extract_documentation
from hpc_site_preflight.documentation.identity import build_query_plan, build_site_identity
from hpc_site_preflight.documentation.models import (
    AllocationRequiredFinding,
    ContextMode,
    CorpusChunk,
    DiscoverySelection,
    DocumentationCitation,
    DocumentationEvidence,
    NetworkFinding,
    SearchResult,
    SubmissionExtractionResult,
)
from hpc_site_preflight.documentation.pipeline import DocumentationPipeline
from hpc_site_preflight.documentation.query_expansion import expand_queries
from hpc_site_preflight.documentation.retrieval import select_context
from hpc_site_preflight.documentation.tools import (
    DocumentationTools,
    LiveWebBackend,
    RecordedWebBackend,
)
from hpc_site_preflight.exceptions import DocumentationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.profiles.documentation import apply_documentation
from hpc_site_preflight.providers.recorded import (
    ModelRecording,
    RecordedModelProvider,
    RecordedModelResponse,
)
from hpc_site_preflight.reporting.tracker import RunTracker

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"
SUBMISSION_FIELDS = (
    "allocation_required",
    "required_submission_options",
    "maximum_walltime_seconds",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _inputs(site_name: str) -> MeasurementBundle:
    directory = SIMULATE_ROOT / site_name
    return MeasurementBundle.model_validate(
        _load(directory / "login-measurements.json")
    )


def _tracker(tmp_path: Path, run_id: str) -> RunTracker:
    return RunTracker(command="test", run_root=tmp_path, quiet=True, run_id=run_id)


@pytest.mark.parametrize(
    ("site_name", "alias", "scheduler", "domain"),
    [
        ("anvil", "Anvil", "slurm", "purdue.edu"),
        ("stampede3", "Stampede3", "slurm", "tacc.utexas.edu"),
        ("notre-dame-crc", "ND CRC", "htcondor", "nd.edu"),
    ],
)
def test_query_plan_is_stable(
    site_name: str,
    alias: str,
    scheduler: str,
    domain: str,
) -> None:
    measurements = _inputs(site_name)
    plan = build_query_plan(build_site_identity(measurements))

    assert [query.topic for query in plan.queries] == [
        "canonical",
        "canonical",
        "submission",
        "submission",
        "resources",
        "resources",
        "storage",
        "storage",
        "networking",
        "networking",
    ]
    assert plan.queries[0].query == f"{alias} official user guide site:{domain}"
    assert scheduler in plan.queries[2].query


def test_user_hints_extend_documentation_identity_and_queries() -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(
        measurements,
        discovery_site_name="Anvil Supercomputer",
        discovery_note="Prefer the RCAC user guide.",
        discovery_keywords=["RCAC", "queues", "RCAC"],
    )
    plan = build_query_plan(identity)

    assert identity.site_name == "Anvil"
    assert identity.discovery_site_name == "Anvil Supercomputer"
    assert identity.aliases[:2] == ["Anvil Supercomputer", "Anvil"]
    assert identity.discovery_note == "Prefer the RCAC user guide."
    assert identity.discovery_keywords == ["RCAC", "queues"]
    assert all(query.query.startswith("Anvil Supercomputer ") for query in plan.queries)
    assert [query.topic for query in plan.queries[-2:]] == ["user", "user"]
    assert "RCAC" in plan.queries[-2].query
    assert "queues" in plan.queries[-1].query


def test_web_tools_enforce_domain_scope_and_budgets() -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend, search_budget=1, page_budget=2)

    results = tools.search_web("job submission")
    assert results
    assert all("example.net" not in result.url for result in results)
    with pytest.raises(DocumentationError, match="budget"):
        tools.search_web("another query")

    sibling = tools.fetch_page(
        "https://docs.rcac.purdue.edu/userguides/negishi/jobs"
    )
    assert sibling.scope == "sibling_site"
    with pytest.raises(DocumentationError, match="target-site"):
        tools.select_fetched_pages([sibling.url])


def test_live_web_backend_searches_and_normalizes_html() -> None:
    html = """<html><head><title>Anvil Guide</title></head><body>
    <nav><a href="/anvil/policies#storage">Policies</a>
    <a href="/anvil/policies#charging">Policies again</a></nav>
    <h1>Jobs</h1><p>Use sbatch to submit.</p>
    <h2>Limits</h2><table><tr><th>Queue</th><th>Time</th></tr>
    <tr><td>shared</td><td>4 days</td></tr></table></body></html>"""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=html,
            request=request,
        )

    backend = LiveWebBackend(
        ["purdue.edu"],
        search_function=lambda query, limit, timeout: [
            SearchResult(
                url="https://docs.rcac.purdue.edu/anvil/jobs",
                title="Anvil jobs",
                snippet="Job submission",
            )
        ],
        transport=httpx.MockTransport(handle),
    )

    results = backend.search("Anvil jobs", 5, 1.0)
    page = backend.fetch(results[0].url, 1.0)

    assert page.title == "Anvil Guide"
    assert any(block.kind == "table" for section in page.sections for block in section.blocks)
    assert any("Use sbatch" in block.text for section in page.sections for block in section.blocks)
    assert page.links[0].url == "https://docs.rcac.purdue.edu/anvil/policies"
    assert len(page.links) == 1


def test_web_tools_canonicalize_and_deduplicate_search_fragments() -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    results = [
        SearchResult(
            url="https://docs.rcac.purdue.edu/anvil/jobs#first",
            title="Jobs",
            snippet="Submission",
        ),
        SearchResult(
            url="https://docs.rcac.purdue.edu/anvil/jobs#second",
            title="Jobs duplicate",
            snippet="Submission",
        ),
    ]
    backend = LiveWebBackend(
        ["purdue.edu"],
        search_function=lambda query, limit, timeout: results,
    )

    canonical = DocumentationTools(identity, backend).search_web("Anvil jobs")

    assert [item.url for item in canonical] == [
        "https://docs.rcac.purdue.edu/anvil/jobs"
    ]


def test_live_web_backend_wraps_network_fetch_errors() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS lookup failed", request=request)

    backend = LiveWebBackend(
        ["purdue.edu"],
        transport=httpx.MockTransport(fail),
    )

    with pytest.raises(DocumentationError, match="network error"):
        backend.fetch("https://anvilcloud.rcac.purdue.edu/guide", 1.0)


def test_discovery_uses_tools_then_one_model_selection(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    plan = build_query_plan(identity)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    selection = DiscoverySelection(
        source_urls=[
            "https://docs.rcac.purdue.edu/anvil/jobs",
            "https://docs.rcac.purdue.edu/anvil/policies",
        ],
        summary="Selected both target-site pages.",
        unanswered_topics=["networking"],
    )
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="single selection test",
            responses=[
                RecordedModelResponse(
                    output_name="documentation_selection",
                    data=selection.model_dump(mode="json"),
                    response_id="selection",
                )
            ],
        )
    )
    tracker = _tracker(tmp_path, "discovery")

    result = DiscoveryAgent(provider).run(
        identity,
        plan,
        tools,
        tracker,
    )

    assert result.termination_reason == "model_selected"
    assert [page.url for page in result.selected_pages] == [
        "https://docs.rcac.purdue.edu/anvil/jobs",
        "https://docs.rcac.purdue.edu/anvil/policies",
    ]
    assert tracker.report.model_usage.requests == 1
    assert tools.searches_used == 10
    assert tools.pages_used == 2


def test_discovery_preserves_pages_when_model_fails(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    tools = DocumentationTools(
        identity,
        RecordedWebBackend.from_path(
            SIMULATE_ROOT / "anvil" / "documentation-web.json"
        ),
    )
    provider = RecordedModelProvider(
        ModelRecording(schema_version="0.1", note="failure", responses=[])
    )

    result = DiscoveryAgent(provider).run(
        identity,
        build_query_plan(identity),
        tools,
        _tracker(tmp_path, "discovery-fallback"),
    )

    assert result.termination_reason == "deterministic_fallback"
    assert len(result.selected_pages) == 2


def test_discovery_corrects_invalid_model_selection(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    tools = DocumentationTools(
        identity,
        RecordedWebBackend.from_path(
            SIMULATE_ROOT / "anvil" / "documentation-web.json"
        ),
    )
    selections = [
        DiscoverySelection(
            source_urls=["https://docs.rcac.purdue.edu/anvil/not-fetched"],
            summary="Invalid selection.",
            unanswered_topics=[],
        ),
        DiscoverySelection(
            source_urls=["https://docs.rcac.purdue.edu/anvil/jobs"],
            summary="Corrected selection.",
            unanswered_topics=["networking"],
        ),
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="selection correction",
            responses=[
                RecordedModelResponse(
                    output_name="documentation_selection",
                    data=selection.model_dump(mode="json"),
                    response_id=f"selection-{index}",
                )
                for index, selection in enumerate(selections)
            ],
        )
    )
    tracker = _tracker(tmp_path, "discovery-correction")

    result = DiscoveryAgent(provider).run(
        identity,
        build_query_plan(identity),
        tools,
        tracker,
    )

    assert result.termination_reason == "model_corrected"
    assert [page.url for page in result.selected_pages] == [
        "https://docs.rcac.purdue.edu/anvil/jobs"
    ]
    assert tracker.report.model_usage.requests == 2


def test_corpus_is_deterministic_and_preserves_tables() -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    pages = [
        tools.fetch_page("https://docs.rcac.purdue.edu/anvil/jobs"),
        tools.fetch_page("https://docs.rcac.purdue.edu/anvil/policies"),
    ]

    first = build_corpus(measurements.site_id, pages)
    second = build_corpus(measurements.site_id, pages)

    assert first == second
    assert first[0].fingerprint
    assert any(chunk.block_kind == "table" for chunk in first[2])
    assert len({chunk.chunk_id for chunk in first[2]}) == len(first[2])


@pytest.mark.parametrize("mode", ["full-corpus", "bm25", "llm-expanded-bm25"])
def test_context_modes_are_stable_and_target_scoped(mode: ContextMode) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    pages = [page for page in backend.recording.pages]
    fetched = [tools.fetch_page(page.url) for page in pages]
    chunks = build_corpus(measurements.site_id, fetched)[2]

    resources = {"maximum_walltime_seconds": {"shared", "wholenode", "gpu"}}
    first = select_context(
        chunks,
        group="submission",
        fields=SUBMISSION_FIELDS,
        mode=mode,
        resources_by_field=resources,
    )
    second = select_context(
        chunks,
        group="submission",
        fields=SUBMISSION_FIELDS,
        mode=mode,
        resources_by_field=resources,
    )

    assert first == second
    assert first.selected_chunk_ids
    assert all(chunk.scope == "target_site" for chunk in first.chunks)
    assert [item.field for item in first.retrievals] == list(SUBMISSION_FIELDS)
    walltime = next(
        item for item in first.retrievals if item.field == "maximum_walltime_seconds"
    )
    assert "doc-anvil-jobs:c2" in {hit.chunk_id for hit in walltime.hits}
    if mode == "full-corpus":
        assert walltime.queries == []
        assert all(hit.score is None for hit in walltime.hits)
        expected = list(
            dict.fromkeys(
                chunk.content_hash
                for chunk in chunks
                if chunk.scope == "target_site"
            )
        )
        assert len(first.chunks) == len(expected)
        assert first.selected_chunk_ids == [chunk.chunk_id for chunk in first.chunks]
    else:
        assert len(walltime.queries) >= 2
        assert all(hit.score is not None for hit in walltime.hits)


def test_llm_expanded_bm25_keeps_base_queries_and_adds_hits() -> None:
    chunks = [
        CorpusChunk(
            chunk_id="base:c1",
            document_id="base",
            source_url="https://example.edu/base",
            title="Queue limits",
            scope="target_site",
            heading_path=["Queues"],
            block_kind="text",
            text="The partition maximum walltime is four days.",
            content_hash="base-hash",
        ),
        CorpusChunk(
            chunk_id="expanded:c1",
            document_id="expanded",
            source_url="https://example.edu/expanded",
            title="Runtime policy",
            scope="target_site",
            heading_path=["Runtime"],
            block_kind="text",
            text="The batch elapsed runtime ceiling is ninety six hours.",
            content_hash="expanded-hash",
        ),
    ]
    resources = {"maximum_walltime_seconds": set()}
    bm25 = select_context(
        chunks,
        group="submission",
        fields=("maximum_walltime_seconds",),
        mode="bm25",
        resources_by_field=resources,
    )
    expanded = select_context(
        chunks,
        group="submission",
        fields=("maximum_walltime_seconds",),
        mode="llm-expanded-bm25",
        resources_by_field=resources,
        expanded_queries_by_field={
            "maximum_walltime_seconds": ["batch elapsed runtime ceiling"]
        },
    )

    retrieval = expanded.retrievals[0]
    assert retrieval.queries[:2] == [
        "partition maximum walltime time limit",
        "queue maximum job duration",
    ]
    assert retrieval.queries[-1] == "batch elapsed runtime ceiling"
    assert set(bm25.selected_chunk_ids) <= set(expanded.selected_chunk_ids)
    assert "expanded:c1" in expanded.selected_chunk_ids


def test_model_expands_bm25_queries_once_and_python_bounds_the_result(
    tmp_path: Path,
) -> None:
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="query expansion",
            responses=[
                RecordedModelResponse(
                    output_name="expand_retrieval_queries",
                    response_id="query-expansion",
                    data={
                        "queries": [
                            {
                                "field": "maximum_walltime_seconds",
                                "query": "partition queue wall clock limit duration",
                            },
                            {
                                "field": "maximum_walltime_seconds",
                                "query": "batch maximum elapsed runtime",
                            },
                            {
                                "field": "maximum_walltime_seconds",
                                "query": "third query is ignored",
                            },
                            {
                                "field": "unknown_field",
                                "query": "unknown field is ignored",
                            },
                        ]
                    },
                )
            ],
        )
    )
    tracker = _tracker(tmp_path, "query-expansion")

    expanded, error = expand_queries(
        site_name="Example HPC",
        scheduler="slurm",
        fields=("maximum_walltime_seconds",),
        resources_by_field={"maximum_walltime_seconds": {"shared"}},
        provider=provider,
        tracker=tracker,
    )

    assert error is None
    assert expanded == {
        "maximum_walltime_seconds": [
            "partition queue wall clock limit duration",
            "batch maximum elapsed runtime",
        ]
    }
    assert tracker.report.model_usage.requests == 1


def test_retrieval_filters_scope_and_deduplicates_content() -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    fetched = [tools.fetch_page(page.url) for page in backend.recording.pages]
    chunks = build_corpus(measurements.site_id, fetched)[2]
    target = next(chunk for chunk in chunks if chunk.scope == "target_site")
    duplicate = target.model_copy(update={"chunk_id": "zzz-duplicate"})

    selection = select_context(
        [*chunks, duplicate],
        group="submission",
        fields=SUBMISSION_FIELDS,
        mode="bm25",
        resources_by_field={"maximum_walltime_seconds": {"shared"}},
    )

    assert all(chunk.scope == "target_site" for chunk in selection.chunks)
    assert len({chunk.content_hash for chunk in selection.chunks}) == len(selection.chunks)
    assert "zzz-duplicate" not in selection.selected_chunk_ids


def test_invalid_span_gets_one_correction(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    pages = [tools.fetch_page("https://docs.rcac.purdue.edu/anvil/policies")]
    chunks = build_corpus(measurements.site_id, pages)[2]
    responses = [
        (
            "extract_submission",
            {
                "allocation_required": {
                    "value": True,
                    "evidence_span_ids": ["missing"],
                    "note": "bad",
                },
                "submission_options": [],
                "partitions": [],
            },
        ),
        (
            "extract_submission",
            {
                "allocation_required": {
                    "value": True,
                    "evidence_span_ids": ["doc-anvil-policies:c1:s1"],
                    "note": "corrected",
                },
                "submission_options": [],
                "partitions": [],
            },
        ),
        ("extract_network", {"network": []}),
        ("extract_operational", {"charging_model": None, "storage": []}),
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="correction test",
            responses=[
                RecordedModelResponse(
                    output_name=name,
                    data=data,
                    response_id=f"response-{index}",
                )
                for index, (name, data) in enumerate(responses)
            ],
        )
    )

    result = extract_documentation(
        site_id=measurements.site_id,
        site_name=measurements.site_facts.site_name,
        scheduler=measurements.scheduler_type,
        partition_names={"shared", "wholenode", "gpu"},
        storage_names={"home", "scratch"},
        chunks=chunks,
        context_mode="full-corpus",
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        provider=provider,
        tracker=_tracker(tmp_path, "correction"),
    )

    assert isinstance(result.findings[0], AllocationRequiredFinding)
    assert result.findings[0].allocation_required is True
    assert result.findings[0].citations[0].quote.startswith("Anvil requires")
    assert any("unknown evidence span" in error for error in result.rejected)


def test_finding_must_cite_context_retrieved_for_its_field(tmp_path: Path) -> None:
    chunks = [
        CorpusChunk(
            chunk_id="doc-allocation:c1",
            document_id="doc-allocation",
            source_url="https://docs.example.edu/site/allocation",
            title="Allocation policy",
            scope="target_site",
            heading_path=["Allocation"],
            block_kind="text",
            text="Every job requires a project allocation.",
            content_hash="allocation",
        ),
        CorpusChunk(
            chunk_id="doc-limits:c1",
            document_id="doc-limits",
            source_url="https://docs.example.edu/site/limits",
            title="Queue limits",
            scope="target_site",
            heading_path=["Partition limits"],
            block_kind="text",
            text="The shared partition has a maximum walltime of four days.",
            content_hash="limits",
        ),
    ]
    responses = [
        (
            "extract_submission",
            {
                "allocation_required": {
                    "value": True,
                    "evidence_span_ids": ["doc-limits:c1:s1"],
                    "note": "Wrong field context.",
                },
                "submission_options": [],
                "partitions": [],
            },
        ),
        (
            "extract_submission",
            {"allocation_required": None, "submission_options": [], "partitions": []},
        ),
        ("extract_network", {"network": []}),
        ("extract_operational", {"charging_model": None, "storage": []}),
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="field-local citation",
            responses=[
                RecordedModelResponse(
                    output_name=name,
                    data=data,
                    response_id=f"field-context-{index}",
                )
                for index, (name, data) in enumerate(responses)
            ],
        )
    )

    result = extract_documentation(
        site_id="example",
        site_name="Example",
        scheduler="slurm",
        partition_names={"shared"},
        storage_names=set(),
        chunks=chunks,
        context_mode="bm25",
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        provider=provider,
        tracker=_tracker(tmp_path, "field-local-citation"),
    )

    assert any("not retrieved for this field" in error for error in result.rejected)


@pytest.mark.parametrize(
    ("site_name", "mode"),
    [
        (site_name, mode)
        for site_name in ("anvil", "stampede3", "notre-dame-crc")
        for mode in ("full-corpus", "bm25", "llm-expanded-bm25")
    ],
)
def test_end_to_end_documentation_profile_is_reproducible(
    site_name: str,
    mode: ContextMode,
    tmp_path: Path,
) -> None:
    measurements = _inputs(site_name)
    directory = SIMULATE_ROOT / site_name
    pipeline = DocumentationPipeline(
        measurements=measurements,
        model_provider=RecordedModelProvider.from_path(directory / "documentation-model.json"),
        web_backend=RecordedWebBackend.from_path(directory / "documentation-web.json"),
        corpus_directory=tmp_path / f"{site_name}-{mode}" / "corpus",
        model_mode="simulate",
        model_provider_name="recorded",
        model=None,
        web_mode="simulate",
    )
    documentation = pipeline.build(
        _tracker(tmp_path, f"{site_name}-{mode}"),
        context_mode=mode,
    )
    profile, report = compile_profile(measurements)
    profile, report = apply_documentation(profile, report, documentation)

    assert documentation.rejected == []
    assert documentation.findings
    assert any(item.source_type == "documentation" for item in report.evidence)
    assert all(
        item.trust == "illustrative"
        for item in report.evidence
        if item.source_type == "documentation"
    )
    assert all(citation.quote for item in documentation.findings for citation in item.citations)
    assert len(documentation.retrieval) == (7 if site_name == "notre-dame-crc" else 8)
    assert any(not hit.cited for item in documentation.retrieval for hit in item.hits)
    assert all(
        any(
            hit.cited and hit.chunk_id == citation.chunk_id
            for retrieval in documentation.retrieval
            for hit in retrieval.hits
        )
        for finding in documentation.findings
        for citation in finding.citations
    )
    if site_name == "anvil":
        walltime = next(
            item
            for item in documentation.retrieval
            if item.field == "maximum_walltime_seconds"
        )
        assert "doc-anvil-jobs:c2" in {hit.chunk_id for hit in walltime.hits}
        assert any(hit.cited for hit in walltime.hits)
        shared = next(item for item in profile.partitions if item.name == "shared")
        assert shared.maximum_walltime_seconds == 345600
        assert profile.accounting.charging_model == "ACCESS service units"
        documentation_paths = {
            item.field_path
            for item in report.evidence
            if item.source_type == "documentation"
        }
        assert documentation_paths == {
            "/accounting/allocation_required",
            "/accounting/charging_model",
            "/partitions/shared/maximum_walltime_seconds",
            "/partitions/wholenode/maximum_walltime_seconds",
            "/storage/scratch/purge_after_days",
            "/submission_options/account/required",
            "/submission_options/partition/required",
        }
        assert documentation_paths <= {item.field for item in profile.field_evidence}
        account = next(item for item in profile.submission_options if item.name == "account")
        partition = next(
            item for item in profile.submission_options if item.name == "partition"
        )
        assert account.required is True
        assert partition.required is True
    elif site_name == "stampede3":
        scratch = next(item for item in profile.storage if item.name == "scratch")
        assert scratch.purge_after_days == 10
    else:
        assert profile.accounting.allocation_required is False


def test_submission_extraction_schema_is_typed_and_allows_silence() -> None:
    silent = SubmissionExtractionResult.model_validate(
        {"allocation_required": None, "submission_options": [], "partitions": []}
    )

    assert silent.allocation_required is None
    with pytest.raises(ValidationError):
        SubmissionExtractionResult.model_validate(
            {
                "allocation_required": {
                    "value": "yes",
                    "evidence_span_ids": ["span"],
                    "note": "Invalid boolean.",
                },
                "submission_options": [],
                "partitions": [],
            }
        )

    schema = SubmissionExtractionResult.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema["$defs"].values():
        assert set(definition["required"]) == set(definition["properties"])


def test_documented_network_findings_fill_structured_profile() -> None:
    measurements = _inputs("anvil")
    citation = DocumentationCitation(
        span_id="network:c1:s1",
        chunk_id="network:c1",
        url="https://docs.rcac.purdue.edu/anvil/network",
        title="Network policy",
        heading="Compute networking",
        quote="Compute workers can connect to a manager on the login system.",
    )
    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        context_mode="bm25",
        findings=[
            NetworkFinding(
                name="manager_worker",
                available=True,
                note="Documented manager connection.",
                citations=[citation],
            )
        ],
        rejected=[],
        unresolved=[],
        selected_chunk_ids=["network:c1"],
        retrieval=[],
    )

    profile, report = compile_profile(measurements)
    profile, _ = apply_documentation(profile, report, documentation)

    assert profile.network.login_compute.tcp_connect is True
    assert any(
        link.field == "/network/login_compute/tcp_connect"
        for link in profile.field_evidence
    )
