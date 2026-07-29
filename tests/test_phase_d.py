"""Offline tests for the complete Phase D documentation pipeline."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from hpc_site_preflight.documentation.corpus import (
    build_corpus,
    load_corpus,
    write_corpus,
)
from hpc_site_preflight.documentation.discovery_agent import DiscoveryAgent
from hpc_site_preflight.documentation.extraction import (
    _canonical_option_name,
    _correctable_errors,
    _valid_unmapped_syntax,
    _validate_group,
    extract_documentation,
)
from hpc_site_preflight.documentation.identity import (
    build_query_plan,
    build_site_identity,
    classify_source,
)
from hpc_site_preflight.documentation.models import (
    AllocationRequiredFinding,
    ContextMode,
    CorpusChunk,
    DiscoverySelection,
    DocumentationCitation,
    DocumentationEvidence,
    EvidenceSpan,
    FieldRetrieval,
    HTCondorPolicyFinding,
    NetworkExtractionResult,
    NetworkFinding,
    OperationalExtractionResult,
    PartitionFinding,
    RecordedPage,
    RetrievalHit,
    SearchResult,
    StoragePolicyFinding,
    SubmissionExtractionResult,
    SubmissionOptionFinding,
    UnmappedSubmissionOptionFinding,
)
from hpc_site_preflight.documentation.pipeline import DocumentationPipeline
from hpc_site_preflight.documentation.query_expansion import expand_queries
from hpc_site_preflight.documentation.retrieval import (
    base_queries,
    batch_full_corpus,
    select_context,
)
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


def _chunk(
    chunk_id: str,
    text: str,
    *,
    block_kind: str = "text",
) -> CorpusChunk:
    return CorpusChunk(
        chunk_id=chunk_id,
        document_id="stampede-guide",
        source_url="https://docs.tacc.utexas.edu/hpc/stampede3",
        title="Stampede3 User Guide",
        scope="target_site",
        heading_path=["Stampede3"],
        block_kind=block_kind,
        text=text,
        content_hash=f"{chunk_id}-hash",
    )


def _span(chunk_id: str, quote: str, *, heading: str) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=f"{chunk_id}:s1",
        chunk_id=chunk_id,
        source_url="https://docs.tacc.utexas.edu/hpc/stampede3",
        title="Stampede3 User Guide",
        heading=heading,
        scope="target_site",
        quote=quote,
    )


class _FollowUpWebBackend:
    """Expose one page initially and a second page only for the model's follow-up query."""

    def __init__(self) -> None:
        self.recorded = RecordedWebBackend.from_path(
            SIMULATE_ROOT / "anvil" / "documentation-web.json"
        )

    def search(
        self,
        query: str,
        limit: int,
        timeout_seconds: float,
    ) -> list[SearchResult]:
        del limit, timeout_seconds
        if "quota lifecycle details" in query.lower():
            return [
                SearchResult(
                    url="https://docs.rcac.purdue.edu/anvil/policies",
                    title="Anvil policies",
                    snippet="Storage and allocation policies.",
                )
            ]
        return [
            SearchResult(
                url="https://docs.rcac.purdue.edu/anvil/jobs",
                title="Anvil jobs",
                snippet="Job submission guide.",
            )
        ]

    def fetch(self, url: str, timeout_seconds: float) -> RecordedPage:
        return self.recorded.fetch(url, timeout_seconds)


@pytest.mark.parametrize(
    ("site_name", "alias", "scheduler", "domain"),
    [
        ("anvil", "Anvil", "slurm", "purdue.edu"),
        ("stampede3", "TACC Stampede3", "slurm", "utexas.edu"),
        ("notre-dame-crc", "Notre Dame CRC", "htcondor", "nd.edu"),
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


def test_preferred_filename_stem_establishes_target_site_scope() -> None:
    identity = build_site_identity(_inputs("notre-dame-crc"))

    scope = classify_source(
        identity,
        "https://docs.crc.nd.edu/resources/condor.html",
        "HTCondor",
        "Submit jobs to the CRC pool.",
    )

    assert scope == "target_site"


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


def test_corpus_preserves_policy_tables_late_in_long_pages() -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    url = "https://docs.rcac.purdue.edu/anvil/jobs"
    page = RecordedPage(
        url=url,
        title="Anvil jobs",
        fetched_at="2026-07-29T12:00:00Z",
        sections=[
            {
                "heading_path": ["Anvil", "Overview"],
                "blocks": [{"kind": "text", "text": "overview " * 4_000}],
            },
            {
                "heading_path": ["Anvil", "Common sbatch options"],
                "blocks": [
                    {
                        "kind": "table",
                        "text": "Option | Policy\n--gres | unsupported",
                    }
                ],
            },
        ],
    )

    class LongPageBackend:
        def search(
            self, query: str, limit: int, timeout_seconds: float
        ) -> list[SearchResult]:
            del query, limit, timeout_seconds
            return []

        def fetch(self, requested_url: str, timeout_seconds: float) -> RecordedPage:
            del requested_url, timeout_seconds
            return page

    fetched = DocumentationTools(identity, LongPageBackend()).fetch_page(url)
    _, _, chunks = build_corpus("anvil", [fetched])

    assert fetched.text_truncated is False
    assert any("--gres | unsupported" in chunk.text for chunk in chunks)


def test_stampede_policy_retrieval_finds_options_gpus_and_storage() -> None:
    chunks = [
        _chunk(
            "options",
            "Common sbatch Options\n-N | Required. Number of nodes.\n"
            "--gres | Stampede3 does not support this option.",
            block_kind="table",
        ),
        _chunk(
            "h100",
            "H100 compute nodes\nGPU: | 4x NVIDIA H100 SXM5",
            block_kind="table",
        ),
        _chunk(
            "storage",
            "$HOME, $WORK, and $SCRATCH are available from all compute nodes.",
        ),
    ]

    submission = select_context(
        chunks,
        scheduler="slurm",
        group="submission",
        fields=("required_submission_options", "gpu_count_per_node", "gpu_models"),
        mode="bm25",
        resources_by_field={
            "gpu_count_per_node": {"h100"},
            "gpu_models": {"h100"},
        },
    )
    operational = select_context(
        chunks,
        scheduler="slurm",
        group="operational",
        fields=("storage_compute_visible",),
        mode="bm25",
        resources_by_field={"storage_compute_visible": {"home", "scratch", "work"}},
    )

    hits = {
        retrieval.field: {hit.chunk_id for hit in retrieval.hits}
        for retrieval in submission.retrievals
    }
    assert "options" in hits["required_submission_options"]
    assert "h100" in hits["gpu_count_per_node"]
    assert "h100" in hits["gpu_models"]
    assert "storage" in {
        hit.chunk_id for hit in operational.retrievals[0].hits
    }


def test_typed_stampede_proposals_accept_resources_storage_and_open_options() -> None:
    option_span = _span(
        "options",
        "--gres | Stampede3 does not support this option.",
        heading="Common sbatch Options",
    )
    gpu_span = _span(
        "h100",
        "GPU: | 4x NVIDIA H100 SXM5",
        heading="H100 compute nodes",
    )
    storage_span = _span(
        "storage",
        "$HOME, $WORK, and $SCRATCH are available from all compute nodes.",
        heading="File systems",
    )
    submission = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "guaranteed_runtime": None,
            "preemptible": None,
            "submission_options": [
                {
                    "name": "gres",
                    "syntax": ["--gres={resource}"],
                    "requirement": "optional",
                    "support": "unsupported",
                    "condition": None,
                    "evidence_span_ids": [option_span.span_id],
                    "note": "Stampede3 rejects this directive.",
                },
                {
                    "name": "exclusive",
                    "syntax": ["--exclusive"],
                    "requirement": "optional",
                    "support": "supported",
                    "condition": None,
                    "evidence_span_ids": [option_span.span_id],
                    "note": "An unfamiliar literal directive remains reviewable.",
                },
            ],
            "unmapped_options": [],
            "partitions": [
                {
                    "name": "h100",
                    "maximum_walltime_seconds": None,
                    "maximum_nodes_per_job": None,
                    "shared_nodes": None,
                    "gpu_count_per_node": {
                        "value": 4,
                        "evidence_span_ids": [gpu_span.span_id],
                        "note": "The node specification documents four GPUs.",
                    },
                    "gpu_models": {
                        "value": ["NVIDIA H100 SXM5"],
                        "evidence_span_ids": [gpu_span.span_id],
                        "note": "The node specification names the GPU model.",
                    },
                    "features": None,
                }
            ],
        }
    )
    submission_retrievals = [
        FieldRetrieval(
            field=field,
            queries=[field],
            hits=[RetrievalHit(chunk_id=chunk_id, score=1.0)],
        )
        for field, chunk_id in (
            ("required_submission_options", "options"),
            ("gpu_count_per_node", "h100"),
            ("gpu_models", "h100"),
        )
    ]
    validated_submission = _validate_group(
        submission,
        [option_span, gpu_span],
        submission_retrievals,
        "slurm",
        {"h100"},
        {"home", "scratch", "work"},
    )
    operational = OperationalExtractionResult.model_validate(
        {
            "charging_unit": None,
            "charging_model": None,
            "filesystem_storage_charged": None,
            "storage": [
                {
                    "name": "$SCRATCH",
                    "compute_visible": {
                        "value": True,
                        "evidence_span_ids": [storage_span.span_id],
                        "note": "Scratch is available from compute nodes.",
                    },
                    "compute_readable": None,
                    "compute_writable": None,
                    "shared_across_compute_nodes": None,
                    "backup_policy": None,
                    "purge_after_days": None,
                    "purge_condition": None,
                }
            ],
        }
    )
    validated_storage = _validate_group(
        operational,
        [storage_span],
        [
            FieldRetrieval(
                field="storage_compute_visible",
                queries=["storage compute visible"],
                hits=[RetrievalHit(chunk_id="storage", score=1.0)],
            )
        ],
        "slurm",
        {"h100"},
        {"home", "scratch", "work"},
    )

    assert not validated_submission.rejected
    assert not validated_storage.rejected
    assert any(
        isinstance(item, SubmissionOptionFinding)
        and item.name == "gres"
        and item.support == "unsupported"
        for item in validated_submission.findings
    )
    assert any(
        isinstance(item, UnmappedSubmissionOptionFinding)
        and item.documented_name == "exclusive"
        and item.support == "supported"
        for item in validated_submission.findings
    )
    assert {
        item.field
        for item in validated_submission.findings
        if isinstance(item, PartitionFinding)
    } == {"gpu_count_per_node", "gpu_models"}
    assert [
        (item.name, item.field, item.value)
        for item in validated_storage.findings
        if isinstance(item, StoragePolicyFinding)
    ] == [("scratch", "compute_visible", True)]


def test_documentation_fills_stampede_resources_options_storage_and_network() -> None:
    measurements = _inputs("stampede3")
    citation = DocumentationCitation(
        span_id="stampede:c1:s1",
        chunk_id="stampede:c1",
        url="https://docs.tacc.utexas.edu/hpc/stampede3",
        title="Stampede3 User Guide",
        heading="Resources",
        quote="H100 nodes have 4 NVIDIA H100 GPUs; all nodes are fully connected to $SCRATCH.",
    )
    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="live",
        context_mode="bm25",
        findings=[
            PartitionFinding(
                name="h100",
                field="gpu_count_per_node",
                value=4,
                note="Documented node shape.",
                citations=[citation],
            ),
            PartitionFinding(
                name="h100",
                field="gpu_models",
                value=["NVIDIA H100 SXM5"],
                note="Documented node shape.",
                citations=[citation],
            ),
            SubmissionOptionFinding(
                name="gres",
                syntax=["--gres={resource}"],
                requirement="optional",
                support="unsupported",
                note="The scheduler rejects this option.",
                citations=[citation],
            ),
            StoragePolicyFinding(
                name="scratch",
                field="compute_visible",
                value=True,
                note="Scratch is mounted on compute nodes.",
                citations=[citation],
            ),
            NetworkFinding(
                name="worker_worker",
                available=True,
                note="The compute fabric is fully connected.",
                citations=[citation],
            ),
        ],
        rejected=[],
        unresolved=[],
        selected_chunk_ids=["stampede:c1"],
        retrieval=[],
    )

    profile, report = compile_profile(measurements)
    profile, report = apply_documentation(profile, report, documentation)

    assert profile.slurm is not None
    h100 = next(item for item in profile.slurm.partitions if item.name == "h100")
    assert h100.gpu_count_per_node == 4
    assert h100.gpu_models == ["NVIDIA H100 SXM5"]
    gres = next(item for item in profile.slurm.options if item.name == "gres")
    assert gres.support == "unsupported"
    scratch = next(item for item in profile.storage if item.id == "scratch")
    assert scratch.compute_visible is True
    assert profile.network.compute_compute.tcp_connect is True
    assert {
        item.field_path
        for item in report.evidence
        if item.source_type == "documentation"
    } >= {
        "/slurm/partitions/h100/gpu_count_per_node",
        "/slurm/partitions/h100/gpu_models",
        "/slurm/options/gres/support",
        "/storage/scratch/compute_visible",
        "/network/compute_compute/tcp_connect",
    }


def test_measured_partition_resource_wins_and_records_documentation_conflict() -> None:
    raw = _load(SIMULATE_ROOT / "stampede3" / "login-measurements.json")
    h100 = next(item for item in raw["slurm"]["partitions"] if item["name"] == "h100")
    h100["gpu_count_per_node"] = 8
    measurements = MeasurementBundle.model_validate(raw)
    citation = DocumentationCitation(
        span_id="stampede:h100:s1",
        chunk_id="stampede:h100",
        url="https://docs.tacc.utexas.edu/hpc/stampede3",
        title="Stampede3 User Guide",
        heading="H100 compute nodes",
        quote="GPU: 4x NVIDIA H100 SXM5",
    )
    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="live",
        context_mode="bm25",
        findings=[
            PartitionFinding(
                name="h100",
                field="gpu_count_per_node",
                value=4,
                note="Documented node shape.",
                citations=[citation],
            )
        ],
        rejected=[],
        unresolved=[],
        selected_chunk_ids=["stampede:h100"],
        retrieval=[],
    )

    profile, report = compile_profile(measurements)
    profile, report = apply_documentation(profile, report, documentation)

    assert profile.slurm is not None
    h100_profile = next(
        item for item in profile.slurm.partitions if item.name == "h100"
    )
    assert h100_profile.gpu_count_per_node == 8
    assert profile.conflicts[-1].field == "/slurm/partitions/h100/gpu_count_per_node"
    assert profile.conflicts[-1].selected_value == 8
    assert report.conflicts[-1].selection_rule == (
        "measurement_over_documentation_for_observable_resource"
    )


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
        decision="complete",
        follow_up_queries=[],
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


def test_discovery_runs_model_requested_follow_up(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    tools = DocumentationTools(identity, _FollowUpWebBackend())
    selections = [
        DiscoverySelection(
            source_urls=["https://docs.rcac.purdue.edu/anvil/jobs"],
            decision="search_more",
            follow_up_queries=["Anvil quota lifecycle details"],
            summary="Jobs are covered; policy details may be missing.",
            unanswered_topics=["storage policy"],
        ),
        DiscoverySelection(
            source_urls=[
                "https://docs.rcac.purdue.edu/anvil/jobs",
                "https://docs.rcac.purdue.edu/anvil/policies",
            ],
            decision="complete",
            follow_up_queries=[],
            summary="Job and policy documentation are now covered.",
            unanswered_topics=[],
        ),
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="two-step discovery",
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
    tracker = _tracker(tmp_path, "discovery-follow-up")

    result = DiscoveryAgent(provider, max_steps=2).run(
        identity,
        build_query_plan(identity),
        tools,
        tracker,
    )

    assert [page.url for page in result.selected_pages] == [
        "https://docs.rcac.purdue.edu/anvil/jobs",
        "https://docs.rcac.purdue.edu/anvil/policies",
    ]
    assert tracker.report.model_usage.requests == 2
    assert tools.searches_used == 11
    assert tools.pages_used == 2


def test_one_discovery_step_does_not_run_requested_follow_up(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    identity = build_site_identity(measurements)
    tools = DocumentationTools(identity, _FollowUpWebBackend())
    selection = DiscoverySelection(
        source_urls=["https://docs.rcac.purdue.edu/anvil/jobs"],
        decision="search_more",
        follow_up_queries=["Anvil quota lifecycle details"],
        summary="More policy documentation could help.",
        unanswered_topics=["storage policy"],
    )
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="one-step discovery",
            responses=[
                RecordedModelResponse(
                    output_name="documentation_selection",
                    data=selection.model_dump(mode="json"),
                    response_id="selection",
                )
            ],
        )
    )
    tracker = _tracker(tmp_path, "discovery-one-step")

    result = DiscoveryAgent(provider, max_steps=1).run(
        identity,
        build_query_plan(identity),
        tools,
        tracker,
    )

    assert [page.url for page in result.selected_pages] == [
        "https://docs.rcac.purdue.edu/anvil/jobs"
    ]
    assert tracker.report.model_usage.requests == 1
    assert tools.searches_used == 10
    assert tools.pages_used == 1


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
            decision="complete",
            follow_up_queries=[],
            summary="Invalid selection.",
            unanswered_topics=[],
        ),
        DiscoverySelection(
            source_urls=["https://docs.rcac.purdue.edu/anvil/jobs"],
            decision="complete",
            follow_up_queries=[],
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


def test_frozen_corpus_round_trip(tmp_path: Path) -> None:
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
    expected = build_corpus(measurements.site_id, pages)
    write_corpus(tmp_path, *expected)

    assert load_corpus(tmp_path, expected_site_id="anvil") == expected


def test_frozen_corpus_rejects_wrong_site() -> None:
    with pytest.raises(DocumentationError, match="does not match"):
        load_corpus(
            SIMULATE_ROOT / "anvil" / "corpus",
            expected_site_id="stampede3",
        )


def test_bm25_recovers_explicit_mandatory_slurm_options(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    _, _, chunks = load_corpus(
        SIMULATE_ROOT / "anvil" / "corpus",
        expected_site_id="anvil",
    )
    responses = [
        RecordedModelResponse(
            output_name="extract_submission",
            response_id="mandatory-options",
            data={
                "allocation_required": None,
                "submission_options": [
                    {
                        "name": "account",
                        "requirement": "required",
                        "evidence_span_ids": [
                            "doc-anvil-jobs:c42:s1",
                            "doc-anvil-jobs:c43:s1",
                        ],
                        "note": "The mandatory list identifies account.",
                    },
                    {
                        "name": "partition",
                        "requirement": "required",
                        "evidence_span_ids": [
                            "doc-anvil-jobs:c42:s1",
                            "doc-anvil-jobs:c44:s1",
                        ],
                        "note": "The mandatory list identifies partition.",
                    },
                ],
                "unmapped_options": [],
                "partitions": [],
            },
        ),
        RecordedModelResponse(
            output_name="extract_network",
            response_id="empty-network",
            data={"network": []},
        ),
        RecordedModelResponse(
            output_name="extract_operational",
            response_id="empty-operational",
            data={"charging_model": None, "storage": []},
        ),
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="Mandatory option regression",
            responses=responses,
        )
    )

    result = extract_documentation(
        site_id="anvil",
        site_name="Anvil",
        scheduler="slurm",
        partition_names=measurements.partition_names,
        storage_names=measurements.storage_names,
        chunks=chunks,
        context_mode="bm25",
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="live",
        provider=provider,
        tracker=_tracker(tmp_path, "mandatory-options"),
    )

    options = {
        finding.name: finding
        for finding in result.findings
        if isinstance(finding, SubmissionOptionFinding)
    }
    assert set(options) == {"account", "partition"}
    assert all(option.requirement == "required" for option in options.values())
    retrieval = next(
        item for item in result.retrieval if item.field == "required_submission_options"
    )
    assert {
        "doc-anvil-jobs:c43",
        "doc-anvil-jobs:c42",
        "doc-anvil-jobs:c41",
        "doc-anvil-jobs:c44",
    } <= {hit.chunk_id for hit in retrieval.hits}
    assert "required_submission_options" not in result.unresolved


def test_bm25_preserves_unknown_required_slurm_directive(tmp_path: Path) -> None:
    measurements = _inputs("anvil")
    chunks = [
        CorpusChunk(
            chunk_id="site-options:c1",
            document_id="site-options",
            source_url="https://docs.example.edu/jobs",
            title="Site submission rules",
            scope="target_site",
            heading_path=["Required directives"],
            block_kind="text",
            text="Site-specific mandatory directive: #SBATCH --licenses=abaqus.",
            content_hash="site-options-hash",
        )
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="Unknown required directive regression",
            responses=[
                RecordedModelResponse(
                    output_name="extract_submission",
                    response_id="unknown-required-directive",
                    data={
                        "allocation_required": None,
                        "submission_options": [],
                        "unmapped_options": [
                            {
                                "documented_name": "licenses",
                                "documented_syntax": [
                                    "#SBATCH --licenses=abaqus"
                                ],
                                "requirement": "required",
                                "evidence_span_ids": ["site-options:c1:s1"],
                                "note": "The site explicitly requires this directive.",
                            }
                        ],
                        "partitions": [],
                    },
                )
            ],
        )
    )
    tracker = _tracker(tmp_path, "unknown-required-directive")

    result = extract_documentation(
        site_id="anvil",
        site_name="Anvil",
        scheduler="slurm",
        partition_names=measurements.partition_names,
        storage_names=set(),
        chunks=chunks,
        context_mode="bm25",
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        provider=provider,
        tracker=tracker,
    )

    findings = [
        finding
        for finding in result.findings
        if isinstance(finding, UnmappedSubmissionOptionFinding)
    ]
    assert len(findings) == 1
    assert findings[0].documented_name == "licenses"
    assert findings[0].documented_syntax == ["#SBATCH --licenses=abaqus"]
    assert tracker.report.model_usage.requests == 1

    profile, report = compile_profile(measurements)
    profile, _ = apply_documentation(profile, report, result)
    assert profile.slurm is not None
    assert profile.slurm.unmapped_options[0].documented_name == "licenses"


def test_bm25_skips_model_calls_without_evidence(tmp_path: Path) -> None:
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="No responses should be consumed.",
            responses=[],
        )
    )
    tracker = _tracker(tmp_path, "empty-bm25")

    result = extract_documentation(
        site_id="notre-dame-crc",
        site_name="Notre Dame CRC",
        scheduler="htcondor",
        partition_names=set(),
        storage_names={"home"},
        chunks=[],
        context_mode="bm25",
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        provider=provider,
        tracker=tracker,
    )

    assert result.findings == []
    assert tracker.report.model_usage.requests == 0


@pytest.mark.parametrize(
    ("quote", "accepted"),
    [
        ("Submit jobs with condor_submit.", False),
        ("Compute nodes cannot connect to login nodes.", True),
    ],
)
def test_false_network_finding_requires_explicit_negative_text(
    quote: str,
    accepted: bool,
) -> None:
    span = EvidenceSpan(
        span_id="network:c1:s1",
        chunk_id="network:c1",
        source_url="https://docs.example.edu/condor/network",
        title="Network",
        heading="Network",
        scope="target_site",
        quote=quote,
    )
    result = NetworkExtractionResult.model_validate(
        {
            "network": [
                {
                    "name": "manager_worker",
                    "available": False,
                    "evidence_span_ids": [span.span_id],
                    "note": "Network policy.",
                }
            ]
        }
    )

    validated = _validate_group(
        result,
        [span],
        [
            FieldRetrieval(
                field="manager_worker_connectivity",
                queries=["login compute connectivity"],
                hits=[RetrievalHit(chunk_id=span.chunk_id, score=1.0)],
            )
        ],
        "htcondor",
        set(),
        set(),
    )

    assert bool(validated.findings) is accepted
    assert bool(validated.rejected) is not accepted


def test_fully_connected_nodes_cannot_validate_worker_disconnect() -> None:
    span = EvidenceSpan(
        span_id="network:c1:s1",
        chunk_id="network:c1",
        source_url="https://docs.example.edu/network",
        title="Network",
        heading="Compute fabric",
        scope="target_site",
        quote="The compute nodes are fully connected with no oversubscription.",
    )
    result = NetworkExtractionResult.model_validate(
        {
            "network": [
                {
                    "name": "worker_worker",
                    "available": False,
                    "evidence_span_ids": [span.span_id],
                    "note": "Contradicts the cited text.",
                }
            ]
        }
    )
    validated = _validate_group(
        result,
        [span],
        [
            FieldRetrieval(
                field="worker_worker_connectivity",
                queries=["fully connected"],
                hits=[RetrievalHit(chunk_id=span.chunk_id, score=1.0)],
            )
        ],
        "slurm",
        set(),
        set(),
    )

    assert not validated.findings
    assert "false requires explicit negative documentation" in validated.rejected[0]


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
        scheduler="slurm",
        group="submission",
        fields=SUBMISSION_FIELDS,
        mode=mode,
        resources_by_field=resources,
    )
    second = select_context(
        chunks,
        scheduler="slurm",
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
        scheduler="slurm",
        group="submission",
        fields=("maximum_walltime_seconds",),
        mode="bm25",
        resources_by_field=resources,
    )
    expanded = select_context(
        chunks,
        scheduler="slurm",
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


def test_submission_queries_are_scheduler_scoped() -> None:
    slurm = base_queries("required_submission_options", "slurm", set())
    htcondor = base_queries("required_submission_options", "htcondor", set())

    assert "required scheduler submission directives job file" in slurm
    assert "required scheduler submission directives job file" in htcondor
    assert any("SBATCH" in query for query in slurm)
    assert not any("request_cpus" in query for query in slurm)
    assert any("request_cpus" in query for query in htcondor)
    assert not any("SBATCH" in query for query in htcondor)
    assert any("licenses" in query for query in slurm)
    assert any("ClassAd" in query for query in htcondor)


def test_full_corpus_batches_cover_every_chunk_once() -> None:
    chunks = [
        CorpusChunk(
            chunk_id=f"document:c{index}",
            document_id="document",
            source_url="https://docs.example.edu/site",
            title="Site guide",
            scope="target_site",
            heading_path=["Jobs"],
            block_kind="text",
            text=f"Chunk {index} " + ("content " * 12),
            content_hash=f"hash-{index}",
        )
        for index in range(3)
    ]
    selection = select_context(
        chunks,
        scheduler="slurm",
        group="submission",
        fields=SUBMISSION_FIELDS,
        mode="full-corpus",
    )

    batches = batch_full_corpus(selection, maximum_chars=350)

    assert len(batches) == 3
    assert [
        chunk_id
        for batch in batches
        for chunk_id in batch.selected_chunk_ids
    ] == selection.selected_chunk_ids
    for batch in batches:
        batch_ids = set(batch.selected_chunk_ids)
        assert all(
            {hit.chunk_id for hit in retrieval.hits} == batch_ids
            for retrieval in batch.retrievals
        )


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
        scheduler="slurm",
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
            "extract_full_corpus",
            {
                "submission": {
                    "allocation_required": {
                        "value": True,
                        "evidence_span_ids": ["missing"],
                        "note": "bad",
                    },
                    "submission_options": [],
                    "unmapped_options": [],
                    "partitions": [],
                },
                "network": {"network": []},
                "operational": {"charging_model": None, "storage": []},
            },
        ),
        (
            "extract_full_corpus",
            {
                "submission": {
                    "allocation_required": {
                        "value": True,
                        "evidence_span_ids": ["doc-anvil-policies:c1:s1"],
                        "note": "corrected",
                    },
                    "submission_options": [],
                    "unmapped_options": [],
                    "partitions": [],
                },
                "network": {"network": []},
                "operational": {"charging_model": None, "storage": []},
            },
        ),
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


def test_full_corpus_extracts_multiple_bounded_batches(tmp_path: Path) -> None:
    chunks = [
        CorpusChunk(
            chunk_id="allocation:c1",
            document_id="allocation",
            source_url="https://docs.example.edu/site/allocation",
            title="Allocation policy",
            scope="target_site",
            heading_path=["Allocation"],
            block_kind="text",
            text="Every job requires a project allocation. " + ("detail " * 1000),
            content_hash="allocation",
        ),
        CorpusChunk(
            chunk_id="charging:c1",
            document_id="charging",
            source_url="https://docs.example.edu/site/charging",
            title="Charging policy",
            scope="target_site",
            heading_path=["Charging"],
            block_kind="text",
            text="Jobs consume project service units. " + ("detail " * 1000),
            content_hash="charging",
        ),
    ]
    responses = [
        {
            "submission": {
                "allocation_required": {
                    "value": True,
                    "evidence_span_ids": ["allocation:c1:s1"],
                    "note": "Allocation required.",
                },
                "submission_options": [],
                "unmapped_options": [],
                "partitions": [],
            },
            "network": {"network": []},
            "operational": {"charging_model": None, "storage": []},
        },
        {
            "submission": {
                "allocation_required": None,
                "submission_options": [],
                "unmapped_options": [],
                "partitions": [],
            },
            "network": {"network": []},
            "operational": {
                "charging_model": {
                    "value": "project service units",
                    "evidence_span_ids": ["charging:c1:s1"],
                    "note": "Jobs consume service units.",
                },
                "storage": [],
            },
        },
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="two full-corpus batches",
            responses=[
                RecordedModelResponse(
                    output_name="extract_full_corpus",
                    data=data,
                    response_id=f"full-batch-{index}",
                )
                for index, data in enumerate(responses)
            ],
        )
    )
    tracker = _tracker(tmp_path, "full-corpus-batches")

    result = extract_documentation(
        site_id="example",
        site_name="Example",
        scheduler="slurm",
        partition_names=set(),
        storage_names=set(),
        chunks=chunks,
        context_mode="full-corpus",
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        provider=provider,
        tracker=tracker,
    )

    assert result.selected_chunk_ids == ["allocation:c1", "charging:c1"]
    assert len(result.findings) == 2
    assert result.rejected == []
    assert tracker.report.model_usage.requests == 2


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
                "unmapped_options": [],
                "partitions": [],
            },
        ),
        (
            "extract_submission",
                {
                    "allocation_required": None,
                    "submission_options": [],
                    "unmapped_options": [],
                    "partitions": [],
                },
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
    recording_path = directory / "documentation-model.json"
    if not recording_path.exists():
        pytest.skip(f"No recorded model fixture for {site_name}.")
    pipeline = DocumentationPipeline(
        measurements=measurements,
        model_provider=RecordedModelProvider.from_path(recording_path),
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
    assert len({item.field for item in documentation.retrieval}) == len(
        documentation.retrieval
    )
    assert {
        "allocation_required",
        "required_submission_options",
        "charging_model",
    } <= {item.field for item in documentation.retrieval}
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
        assert profile.slurm is not None
        shared = next(item for item in profile.slurm.partitions if item.name == "shared")
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
            "/slurm/partitions/shared/maximum_walltime_seconds",
            "/slurm/partitions/wholenode/maximum_walltime_seconds",
            "/storage/scratch/purge_after_days",
            "/slurm/options/account/required",
            "/slurm/options/partition/required",
        }
        assert documentation_paths <= {item.profile_field for item in report.links}
        account = next(item for item in profile.slurm.options if item.name == "account")
        partition = next(
            item for item in profile.slurm.options if item.name == "partition"
        )
        assert account.required is True
        assert partition.required is True
    elif site_name == "stampede3":
        scratch = next(item for item in profile.storage if item.id == "scratch")
        assert scratch.purge_after_days == 10
    else:
        assert profile.accounting.allocation_required is False


def test_submission_extraction_schema_is_typed_and_allows_silence() -> None:
    silent = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "submission_options": [],
            "unmapped_options": [],
            "partitions": [],
        }
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
                "unmapped_options": [],
                "partitions": [],
            }
        )
    proposed = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "submission_options": [
                {
                    "name": "Account (-A or --account)",
                    "syntax": ["-A {account}", "--account={account}"],
                    "requirement": "required",
                    "support": "supported",
                    "condition": None,
                    "evidence_span_ids": ["span"],
                    "note": "The model may propose a documented display name.",
                }
            ],
            "unmapped_options": [],
            "partitions": [],
        }
    )
    assert proposed.submission_options[0].name == "Account (-A or --account)"

    assert (
        _canonical_option_name(
            "Account (-A or --account)",
            ["-A {account}", "--account={account}"],
            {"account", "partition"},
        )
        == "account"
    )

    schema = SubmissionExtractionResult.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    for definition in schema["$defs"].values():
        assert set(definition["required"]) == set(definition["properties"])


def test_unmapped_options_require_literal_scheduler_syntax() -> None:
    assert _valid_unmapped_syntax(
        "slurm",
        "Dependency",
        ["#SBATCH --dependency=afterok:{job_id}"],
    )
    assert not _valid_unmapped_syntax(
        "slurm",
        "Load rclone",
        ["module load rclone"],
    )
    assert _valid_unmapped_syntax(
        "htcondor",
        "Project name",
        ['+ProjectName = "example"'],
    )
    assert not _valid_unmapped_syntax(
        "htcondor",
        "Create an allocation",
        ["Visit the allocation portal before submitting jobs."],
    )


def test_typed_htcondor_attributes_are_not_valid_slurm_options() -> None:
    span = EvidenceSpan(
        span_id="submission:c1:s1",
        chunk_id="submission:c1",
        source_url="https://docs.example.edu/condor",
        title="HTCondor",
        heading="Submit file",
        scope="target_site",
        quote="should_transfer_files = yes",
    )
    result = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "submission_options": [
                {
                    "name": "should_transfer_files",
                    "requirement": "recommended",
                    "evidence_span_ids": [span.span_id],
                    "note": "The site template recommends file transfer.",
                }
            ],
            "unmapped_options": [],
            "partitions": [],
        }
    )
    retrieval = [
        FieldRetrieval(
            field="required_submission_options",
            queries=["HTCondor submit file"],
            hits=[RetrievalHit(chunk_id=span.chunk_id, score=1.0)],
        )
    ]

    htcondor = _validate_group(result, [span], retrieval, "htcondor", set(), set())
    slurm = _validate_group(result, [span], retrieval, "slurm", set(), set())

    assert [finding.name for finding in htcondor.findings] == [
        "should_transfer_files"
    ]
    assert not slurm.findings
    assert "no literal slurm submission syntax" in slurm.rejected[0]


def test_htcondor_displacement_policy_is_extracted_and_cited() -> None:
    measurements = _inputs("notre-dame-crc")
    quote = (
        "Of course, if you are borrowing machines owned by other people, then "
        "you must accept the possibility that some jobs may be kicked off and "
        "must run elsewhere."
    )
    span = EvidenceSpan(
        span_id="doc-resources-condor-html:c9:s3",
        chunk_id="doc-resources-condor-html:c9",
        source_url="https://docs.crc.nd.edu/resources/condor.html",
        title="HTCondor",
        heading="HTCondor > About Condor",
        scope="target_site",
        quote=quote,
    )
    result = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "guaranteed_runtime": {
                "value": False,
                "evidence_span_ids": [span.span_id],
                "note": "Borrowed machines may displace jobs.",
            },
            "preemptible": {
                "value": True,
                "evidence_span_ids": [span.span_id],
                "note": "Displaced jobs must run elsewhere.",
            },
            "submission_options": [],
            "unmapped_options": [],
            "partitions": [],
        }
    )
    retrievals = [
        FieldRetrieval(
            field=field,
            queries=["jobs kicked off must run elsewhere"],
            hits=[RetrievalHit(chunk_id=span.chunk_id, score=1.0)],
        )
        for field in ("guaranteed_runtime", "preemptible")
    ]

    validated = _validate_group(
        result,
        [span],
        retrievals,
        "htcondor",
        set(),
        set(),
    )
    assert not validated.rejected
    assert [
        (finding.name, finding.value)
        for finding in validated.findings
        if isinstance(finding, HTCondorPolicyFinding)
    ] == [("guaranteed_runtime", False), ("preemptible", True)]

    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="live",
        context_mode="bm25",
        findings=validated.findings,
        rejected=[],
        unresolved=[],
        selected_chunk_ids=[span.chunk_id],
        retrieval=retrievals,
    )
    profile, report = compile_profile(measurements)
    profile, report = apply_documentation(profile, report, documentation)

    assert profile.htcondor is not None
    assert profile.htcondor.guaranteed_runtime is False
    assert profile.htcondor.preemptible is True
    assert profile.htcondor.maximum_walltime == "not_applicable"
    evidence = {
        item.field_path: item
        for item in report.evidence
        if item.source_type == "documentation"
    }
    assert evidence["/htcondor/guaranteed_runtime"].exact_quote == quote
    assert evidence["/htcondor/preemptible"].exact_quote == quote
    assert all(
        item.source_reference == span.source_url for item in evidence.values()
    )


def test_same_heading_context_can_support_one_field_citation() -> None:
    heading = "HTCondor > Example of Submitting a GPU Job"
    prose = EvidenceSpan(
        span_id="condor:c48:s1",
        chunk_id="condor:c48",
        source_url="https://docs.example.edu/condor",
        title="HTCondor",
        heading=heading,
        scope="target_site",
        quote="This is a submission template for running a job with one GPU.",
    )
    code = EvidenceSpan(
        span_id="condor:c49:s1",
        chunk_id="condor:c49",
        source_url=prose.source_url,
        title=prose.title,
        heading=heading,
        scope="target_site",
        quote="request_gpus = 1",
    )
    result = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "submission_options": [
                {
                    "name": "request_gpus",
                    "requirement": "conditional",
                    "evidence_span_ids": [prose.span_id, code.span_id],
                    "note": "GPU jobs request one GPU.",
                }
            ],
            "unmapped_options": [],
            "partitions": [],
        }
    )
    retrieval = [
        FieldRetrieval(
            field="required_submission_options",
            queries=["request_gpus"],
            hits=[RetrievalHit(chunk_id=code.chunk_id, score=1.0)],
        )
    ]

    validated = _validate_group(
        result,
        [prose, code],
        retrieval,
        "htcondor",
        set(),
        set(),
    )

    assert len(validated.findings) == 1
    assert not validated.rejected


def test_same_document_but_different_heading_is_rejected() -> None:
    retrieved = EvidenceSpan(
        span_id="condor:c49:s1",
        chunk_id="condor:c49",
        source_url="https://docs.example.edu/condor",
        title="HTCondor",
        heading="GPU jobs",
        scope="target_site",
        quote="request_gpus = 1",
    )
    unrelated = retrieved.model_copy(
        update={
            "span_id": "condor:c20:s1",
            "chunk_id": "condor:c20",
            "heading": "UGE jobs",
            "quote": "Submit to a GPU queue.",
        }
    )
    result = SubmissionExtractionResult.model_validate(
        {
            "allocation_required": None,
            "submission_options": [
                {
                    "name": "request_gpus",
                    "requirement": "conditional",
                    "evidence_span_ids": [unrelated.span_id, retrieved.span_id],
                    "note": "GPU request.",
                }
            ],
            "unmapped_options": [],
            "partitions": [],
        }
    )

    validated = _validate_group(
        result,
        [unrelated, retrieved],
        [
            FieldRetrieval(
                field="required_submission_options",
                queries=["request_gpus"],
                hits=[RetrievalHit(chunk_id=retrieved.chunk_id, score=1.0)],
            )
        ],
        "htcondor",
        set(),
        set(),
    )

    assert not validated.findings
    assert "evidence was not retrieved for this field" in validated.rejected[0]


def test_false_from_silence_is_not_sent_for_model_correction() -> None:
    errors = [
        "network/outbound_compute: false requires explicit negative documentation, "
        "not documentation silence",
        "submission_options/account: unknown evidence span missing",
    ]

    assert _correctable_errors(errors) == [errors[1]]


@pytest.mark.parametrize("requirement", ["recommended", "conditional"])
def test_non_binary_submission_requirement_does_not_become_false(
    requirement: str,
) -> None:
    measurements = _inputs("notre-dame-crc")
    citation = DocumentationCitation(
        span_id="submission:c1:s1",
        chunk_id="submission:c1",
        url="https://docs.crc.nd.edu/resources/condor.html",
        title="HTCondor",
        heading="GPU jobs",
        quote="request_gpus = 1",
    )
    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        context_mode="bm25",
        findings=[
            SubmissionOptionFinding(
                name="request_gpus",
                requirement=requirement,
                note="Applies to GPU jobs.",
                citations=[citation],
            )
        ],
        rejected=[],
        unresolved=[],
        selected_chunk_ids=["submission:c1"],
        retrieval=[],
    )

    profile, report = compile_profile(measurements)
    profile, report = apply_documentation(profile, report, documentation)
    assert profile.htcondor is not None
    option = next(
        item
        for item in profile.htcondor.submit_attributes
        if item.name == "request_gpus"
    )

    if requirement == "recommended":
        assert option.required is False
        assert not any(
            item.field == "/htcondor/submit_attributes/request_gpus/required"
            for item in profile.unresolved
        )
        assert any(item.source_type == "documentation" for item in report.evidence)
    else:
        assert option.required is None
        assert any(
            item.field == "/htcondor/submit_attributes/request_gpus/required"
            for item in profile.unresolved
        )
        assert not any(item.source_type == "documentation" for item in report.evidence)


def test_unmapped_submission_option_is_preserved_for_review() -> None:
    measurements = _inputs("anvil")
    citation = DocumentationCitation(
        span_id="submission:c1:s1",
        chunk_id="submission:c1",
        url="https://docs.rcac.purdue.edu/anvil/jobs",
        title="Submission policy",
        heading="Mandatory fields",
        quote="Every licensed job must specify --site-license.",
    )
    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="simulate",
        context_mode="bm25",
        findings=[
            UnmappedSubmissionOptionFinding(
                documented_name="Site license",
                documented_syntax=["--site-license={license}"],
                requirement="required",
                note="No reviewed profile mapping exists.",
                citations=[citation],
            )
        ],
        rejected=[],
        unresolved=["required_submission_options"],
        selected_chunk_ids=["submission:c1"],
        retrieval=[],
    )

    profile, report = compile_profile(measurements)
    profile, report = apply_documentation(profile, report, documentation)

    assert profile.slurm is not None
    assert profile.slurm.unmapped_options[0].documented_name == "Site license"
    assert profile.slurm.unmapped_options[0].status == "needs_mapping"
    assert any(
        item.field == "/slurm/unmapped_options"
        and item.action_id == "submission_option_mapping"
        for item in profile.unresolved
    )
    assert any(
        link.profile_field == "/slurm/unmapped_options"
        for link in report.links
    )
    assert any(
        item.field_path == "/slurm/unmapped_options"
        for item in report.evidence
    )


def test_anvil_walltime_reconciliation_records_eight_policy_conflicts() -> None:
    measurements = _inputs("anvil")
    documented_limits = {
        "debug": 7200,
        "gpu-debug": 1800,
        "wholenode": 345600,
        "wide": 43200,
        "shared": 345600,
        "highmem": 172800,
        "gpu": 172800,
        "ai": 172800,
    }
    findings = []
    for name, seconds in documented_limits.items():
        citation = DocumentationCitation(
            span_id=f"limits:{name}:s1",
            chunk_id=f"limits:{name}",
            url="https://docs.rcac.purdue.edu/anvil/jobs",
            title="Anvil partition limits",
            heading=name,
            quote=f"The {name} partition has a maximum walltime of {seconds} seconds.",
        )
        findings.append(
            PartitionFinding(
                name=name,
                maximum_walltime_seconds=seconds,
                note="Documented enforced partition policy.",
                citations=[citation],
            )
        )
    documentation = DocumentationEvidence(
        site_id=measurements.site_id,
        model_mode="simulate",
        model_provider="recorded",
        model=None,
        web_mode="live",
        context_mode="bm25",
        findings=findings,
        rejected=[],
        unresolved=["maximum_walltime_seconds"],
        selected_chunk_ids=[f"limits:{name}" for name in documented_limits],
        retrieval=[],
    )

    profile, report = compile_profile(measurements)
    assert profile.slurm is not None
    assert len(profile.slurm.partitions) == 10
    assert all(
        partition.maximum_walltime_seconds == -1
        for partition in profile.slurm.partitions
    )

    profile, report = apply_documentation(profile, report, documentation)
    partitions = {item.name: item for item in profile.slurm.partitions}
    assert {
        name: partitions[name].maximum_walltime_seconds
        for name in documented_limits
    } == documented_limits
    assert partitions["standard"].maximum_walltime_seconds == -1
    assert partitions["profiling"].maximum_walltime_seconds == -1

    assert len(profile.conflicts) == 8
    assert len(report.conflicts) == 8
    assert {item.field.split("/")[-2] for item in profile.conflicts} == set(
        documented_limits
    )
    assert all(
        [(item.source, item.value) for item in conflict.evidence_values]
        == [("measurement", -1), ("documentation", conflict.selected_value)]
        for conflict in profile.conflicts
    )
    assert not any(
        "/standard/" in item.field or "/profiling/" in item.field
        for item in profile.conflicts
    )


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
        link.profile_field == "/network/login_compute/tcp_connect"
        for link in report.links
    )
