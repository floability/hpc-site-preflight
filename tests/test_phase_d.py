"""Offline tests for the complete Phase D documentation pipeline."""

import json
from pathlib import Path

import httpx
import pytest

from hpc_site_preflight.documentation.corpus import build_corpus
from hpc_site_preflight.documentation.discovery_agent import DiscoveryAgent
from hpc_site_preflight.documentation.extraction import extract_documentation
from hpc_site_preflight.documentation.identity import build_query_plan, build_site_identity
from hpc_site_preflight.documentation.models import (
    ContextMode,
    DiscoverySelection,
    SearchResult,
)
from hpc_site_preflight.documentation.pipeline import DocumentationPipeline
from hpc_site_preflight.documentation.retrieval import select_context
from hpc_site_preflight.documentation.tools import (
    DocumentationTools,
    LiveWebBackend,
    RecordedWebBackend,
)
from hpc_site_preflight.exceptions import DocumentationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.profiles.compiler import compile_profile
from hpc_site_preflight.providers.recorded import (
    ModelRecording,
    RecordedModelProvider,
    RecordedModelResponse,
)
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo

ROOT = Path(__file__).resolve().parents[1]
SIMULATE_ROOT = ROOT / "examples" / "simulate"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _inputs(site_name: str) -> tuple[SiteInfo, MeasurementBundle]:
    directory = SIMULATE_ROOT / site_name
    site = SiteInfo.model_validate(_load(directory / "site-info.json"))
    measurements = MeasurementBundle.model_validate(
        _load(directory / "login-measurements.json")
    )
    return site, measurements


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
    site, measurements = _inputs(site_name)
    plan = build_query_plan(build_site_identity(site, measurements))

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
    site, measurements = _inputs("anvil")
    identity = build_site_identity(
        site,
        measurements,
        discovery_site_name="Anvil Supercomputer",
        discovery_note="Prefer the RCAC user guide.",
        discovery_keywords=["RCAC", "queues", "RCAC"],
    )
    plan = build_query_plan(identity)

    assert identity.site_name == "Purdue Anvil"
    assert identity.discovery_site_name == "Anvil Supercomputer"
    assert identity.aliases[:2] == ["Anvil Supercomputer", "Purdue Anvil"]
    assert identity.discovery_note == "Prefer the RCAC user guide."
    assert identity.discovery_keywords == ["RCAC", "queues"]
    assert all(query.query.startswith("Anvil Supercomputer ") for query in plan.queries)
    assert [query.topic for query in plan.queries[-2:]] == ["user", "user"]
    assert "RCAC" in plan.queries[-2].query
    assert "queues" in plan.queries[-1].query


def test_web_tools_enforce_domain_scope_and_budgets() -> None:
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
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
        tools.finish_discovery([sibling.url], "done", [])


def test_live_web_backend_searches_and_normalizes_html() -> None:
    html = """<html><head><title>Anvil Guide</title></head><body>
    <nav><a href="/anvil/policies">Policies</a></nav>
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


def test_discovery_uses_tools_then_one_model_selection(tmp_path: Path) -> None:
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
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
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
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
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
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
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    pages = [
        tools.fetch_page("https://docs.rcac.purdue.edu/anvil/jobs"),
        tools.fetch_page("https://docs.rcac.purdue.edu/anvil/policies"),
    ]

    first = build_corpus(site.site_id, pages)
    second = build_corpus(site.site_id, pages)

    assert first == second
    assert first[0].fingerprint
    assert any(chunk.block_kind == "table" for chunk in first[2])
    assert len({chunk.chunk_id for chunk in first[2]}) == len(first[2])


@pytest.mark.parametrize("mode", ["full-corpus", "bm25", "schema-expanded-bm25"])
def test_context_modes_are_stable_and_target_scoped(mode: ContextMode) -> None:
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    pages = [page for page in backend.recording.pages]
    fetched = [tools.fetch_page(page.url) for page in pages]
    chunks = build_corpus(site.site_id, fetched)[2]

    first = select_context(chunks, group="submission", mode=mode)
    second = select_context(chunks, group="submission", mode=mode)

    assert first == second
    assert first.selected_chunk_ids
    assert all(chunk.scope == "target_site" for chunk in first.chunks)


def test_invalid_span_gets_one_correction(tmp_path: Path) -> None:
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    pages = [tools.fetch_page("https://docs.rcac.purdue.edu/anvil/policies")]
    chunks = build_corpus(site.site_id, pages)[2]
    responses = [
        (
            "extract_submission",
            {
                "findings": [
                    {
                        "field": "allocation_required",
                        "resource": None,
                        "value": True,
                        "evidence_span_ids": ["missing"],
                        "note": "bad",
                    }
                ]
            },
        ),
        (
            "extract_submission",
            {
                "findings": [
                    {
                        "field": "allocation_required",
                        "resource": None,
                        "value": True,
                        "evidence_span_ids": ["doc-anvil-policies:c1:s1"],
                        "note": "corrected",
                    }
                ]
            },
        ),
        ("extract_network", {"findings": []}),
        ("extract_operational", {"findings": []}),
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
        site_id=site.site_id,
        site_name=site.site_name,
        scheduler=site.scheduler,
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

    assert result.findings[0].field == "allocation_required"
    assert result.findings[0].citations[0].quote.startswith("Anvil requires")
    assert any("unknown evidence span" in error for error in result.rejected)


@pytest.mark.parametrize(
    ("site_name", "mode"),
    [
        (site_name, mode)
        for site_name in ("anvil", "stampede3", "notre-dame-crc")
        for mode in ("full-corpus", "bm25", "schema-expanded-bm25")
    ],
)
def test_end_to_end_documentation_profile_is_reproducible(
    site_name: str,
    mode: ContextMode,
    tmp_path: Path,
) -> None:
    site, measurements = _inputs(site_name)
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
        site,
        _tracker(tmp_path, f"{site_name}-{mode}"),
        context_mode=mode,
    )
    profile, report = compile_profile(site, measurements, documentation)

    assert documentation.rejected == []
    assert documentation.findings
    assert any(item.source_type == "documentation" for item in report.evidence)
    assert all(
        item.trust == "illustrative"
        for item in report.evidence
        if item.source_type == "documentation"
    )
    assert all(citation.quote for item in documentation.findings for citation in item.citations)
    if site_name == "anvil":
        shared = next(item for item in profile.partitions if item.name == "shared")
        assert shared.maximum_walltime_seconds == 345600
        assert profile.accounting.charging_model == "ACCESS service units"
    elif site_name == "stampede3":
        scratch = next(item for item in profile.storage if item.name == "scratch")
        assert scratch.purge_after_days == 10
    else:
        assert profile.accounting.allocation_required is False
