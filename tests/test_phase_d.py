"""Offline tests for the complete Phase D documentation pipeline."""

import json
from pathlib import Path

import pytest

from hpc_site_preflight.documentation.corpus import build_corpus
from hpc_site_preflight.documentation.discovery import DiscoveryAgent
from hpc_site_preflight.documentation.extraction import extract_documentation
from hpc_site_preflight.documentation.identity import build_query_plan, build_site_identity
from hpc_site_preflight.documentation.models import ContextMode, DiscoveryDecision
from hpc_site_preflight.documentation.policy_agent_adapter import PolicyAgentAdapter
from hpc_site_preflight.documentation.retrieval import select_context
from hpc_site_preflight.documentation.web import DocumentationTools, RecordedWebBackend
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
        "submission",
        "resources",
        "storage",
        "networking",
    ]
    assert plan.queries[0].query == (
        f"{alias} {scheduler} submit job account queue partition site:{domain}"
    )


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


def test_discovery_preserves_partial_pages_at_turn_limit(tmp_path: Path) -> None:
    site, measurements = _inputs("anvil")
    identity = build_site_identity(site, measurements)
    plan = build_query_plan(identity)
    backend = RecordedWebBackend.from_path(
        SIMULATE_ROOT / "anvil" / "documentation-web.json"
    )
    tools = DocumentationTools(identity, backend)
    decisions = [
        DiscoveryDecision(
            action="fetch_page",
            query=None,
            url="https://docs.rcac.purdue.edu/anvil/jobs",
            source_urls=[],
            summary=None,
            unanswered_topics=[],
        ),
        DiscoveryDecision(
            action="search_web",
            query="network policy",
            url=None,
            source_urls=[],
            summary=None,
            unanswered_topics=[],
        ),
    ]
    provider = RecordedModelProvider(
        ModelRecording(
            schema_version="0.1",
            note="turn limit test",
            responses=[
                RecordedModelResponse(
                    output_name="discovery_action",
                    data=decision.model_dump(mode="json"),
                    response_id=f"turn-{index}",
                )
                for index, decision in enumerate(decisions)
            ],
        )
    )

    result = DiscoveryAgent(provider, maximum_turns=2).run(
        identity,
        plan,
        tools,
        _tracker(tmp_path, "discovery"),
    )

    assert result.termination_reason == "turn_limit"
    assert [page.url for page in result.selected_pages] == [
        "https://docs.rcac.purdue.edu/anvil/jobs"
    ]


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
    adapter = PolicyAgentAdapter(
        measurements=measurements,
        model_provider=RecordedModelProvider.from_path(directory / "documentation-model.json"),
        web_backend=RecordedWebBackend.from_path(directory / "documentation-web.json"),
        corpus_directory=tmp_path / f"{site_name}-{mode}" / "corpus",
    )
    documentation = adapter.build(
        site,
        _tracker(tmp_path, f"{site_name}-{mode}"),
        context_mode=mode,
    )
    profile, report = compile_profile(site, measurements, documentation)

    assert documentation.rejected == []
    assert documentation.findings
    assert any(item.source_type == "documentation" for item in report.evidence)
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
