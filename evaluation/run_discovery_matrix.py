#!/usr/bin/env python3
"""Run compact, discovery-only repetitions without retaining page bodies."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from hpc_site_preflight.documentation.discovery_agent import DiscoveryAgent
from hpc_site_preflight.documentation.identity import build_query_plan, build_site_identity
from hpc_site_preflight.documentation.models import QueryPlan, RecordedPage, SearchResult
from hpc_site_preflight.documentation.tools import (
    DEFAULT_PAGE_BUDGET,
    DEFAULT_SEARCH_BUDGET,
    FOLLOW_UP_PAGE_BUDGET,
    FOLLOW_UP_SEARCH_BUDGET,
    DocumentationTools,
    LiveWebBackend,
)
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.providers.base import (
    ModelProvider,
    ResultModel,
    StructuredModelRequest,
)
from hpc_site_preflight.providers.registry import create_live_model_provider
from hpc_site_preflight.reporting.tracker import RunTracker

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "evaluation" / "discovery-results"
RUN_ROOT = OUTPUT_ROOT / "run-reports"
RUN_LOG = OUTPUT_ROOT / "run-log.csv"
DEFAULT_INPUTS = {
    "stampede3": ROOT / "examples" / "simulate" / "stampede3" / "login-measurements.json",
    "anvil": ROOT / "examples" / "simulate" / "anvil" / "login-measurements.json",
    "notre-dame-crc": (
        ROOT / "examples" / "simulate" / "notre-dame-crc" / "login-measurements.json"
    ),
}
TOPIC_TERMS = {
    "submission": (
        "sbatch",
        "batch job",
        "job script",
        "submit a job",
        "condor_submit",
    ),
    "resources": (
        "partition",
        "queue",
        "compute node",
        "cpu",
        "memory",
        "gpu",
    ),
    "storage": (
        "scratch",
        "$work",
        "work directory",
        "home directory",
        "filesystem",
        "file system",
        "storage",
    ),
    "networking": (
        "network",
        "firewall",
        "tcp",
        "port",
        "internet",
        "connectivity",
    ),
}
CSV_COLUMNS = (
    "run_id",
    "site",
    "model",
    "rep",
    "status",
    "decision",
    "discovery_steps",
    "search_calls",
    "unique_search_results",
    "fetch_attempts",
    "fetch_successes",
    "selected_pages",
    "coverage_count",
    "coverage_fraction",
    "covered_topics",
    "missing_topics",
    "model_unanswered_topics",
    "search_url_jaccard_to_rep1",
    "selected_url_jaccard_to_rep1",
    "topic_jaccard_to_rep1",
    "model_calls",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "latency_sec",
    "termination_reason",
    "notes",
)


class CapturingWebBackend:
    """Delegate live web access while retaining only query and URL metadata."""

    def __init__(self, backend: LiveWebBackend) -> None:
        self.backend = backend
        self.searches: list[dict[str, Any]] = []
        self.fetches: list[dict[str, str]] = []

    def search(
        self,
        query: str,
        limit: int,
        timeout_seconds: float,
    ) -> list[SearchResult]:
        try:
            results = self.backend.search(query, limit, timeout_seconds)
        except Exception as exc:
            self.searches.append(
                {
                    "query": query,
                    "result_urls": [],
                    "status": "failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        self.searches.append(
            {
                "query": query,
                "result_urls": [result.url for result in results],
                "status": "completed",
            }
        )
        return results

    def fetch(self, url: str, timeout_seconds: float) -> RecordedPage:
        try:
            page = self.backend.fetch(url, timeout_seconds)
        except Exception as exc:
            self.fetches.append(
                {
                    "requested_url": url,
                    "status": "failed",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        self.fetches.append(
            {
                "requested_url": url,
                "final_url": page.url,
                "status": "completed",
            }
        )
        return page


class CapturingModelProvider(ModelProvider):
    """Delegate model calls while retaining their small structured decisions."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider
        self.decisions: list[dict[str, Any]] = []

    def generate_structured(
        self,
        request: StructuredModelRequest,
        result_type: type[ResultModel],
        tracker: RunTracker,
    ) -> ResultModel:
        result = self.provider.generate_structured(request, result_type, tracker)
        self.decisions.append(
            {
                "output_name": request.output_name,
                "correction": "INVALID SELECTION:" in request.user_prompt,
                "result": result.model_dump(mode="json"),
            }
        )
        return result


def load_measurements(path: Path) -> MeasurementBundle:
    """Load one already validated evaluation measurement fixture."""

    return MeasurementBundle.model_validate_json(path.read_text(encoding="utf-8"))


def page_topic_coverage(pages: list[Any]) -> dict[str, dict[str, list[str]]]:
    """Return matched terms and selected URLs for each covered policy topic."""

    coverage: dict[str, dict[str, list[str]]] = {}
    for topic, terms in TOPIC_TERMS.items():
        matched_terms: set[str] = set()
        source_urls: set[str] = set()
        for page in pages:
            text = " ".join(
                [
                    page.url,
                    page.title,
                    *(
                        " ".join(
                            [
                                *section.heading_path,
                                *(block.text for block in section.blocks),
                            ]
                        )
                        for section in page.sections
                    ),
                ]
            ).lower()
            page_matches = {term for term in terms if term in text}
            if page_matches:
                matched_terms.update(page_matches)
                source_urls.add(page.url)
        if matched_terms:
            coverage[topic] = {
                "matched_terms": sorted(matched_terms),
                "source_urls": sorted(source_urls),
            }
    return coverage


def run_one(
    *,
    site: str,
    model: str,
    rep: int,
    measurements: MeasurementBundle,
    max_steps: int,
    keywords: list[str],
) -> dict[str, Any]:
    """Run one independent live discovery and return a body-free summary."""

    tracker = RunTracker(
        command="discovery evaluation",
        mode=f"site={site}, model={model}, web=live",
        run_root=RUN_ROOT,
        quiet=False,
    )
    model_provider = CapturingModelProvider(create_live_model_provider(model))
    identity = build_site_identity(measurements, discovery_keywords=keywords)
    plan: QueryPlan = build_query_plan(identity)
    web_backend = CapturingWebBackend(LiveWebBackend(identity.allowed_domains))
    follow_up_steps = max_steps - 1
    tools = DocumentationTools(
        identity,
        web_backend,
        search_budget=DEFAULT_SEARCH_BUDGET
        + FOLLOW_UP_SEARCH_BUDGET * follow_up_steps,
        page_budget=DEFAULT_PAGE_BUDGET + FOLLOW_UP_PAGE_BUDGET * follow_up_steps,
    )

    try:
        result = DiscoveryAgent(model_provider, max_steps=max_steps).run(
            identity,
            plan,
            tools,
            tracker,
        )
    except Exception as exc:
        tracker.record_run_error(exc)
        tracker.finalize(status="failed")
        return {
            "run_id": tracker.run_id,
            "site": site,
            "model": model,
            "rep": rep,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

    tracker.finalize(status="completed")
    coverage = page_topic_coverage(result.selected_pages)
    covered_topics = sorted(coverage)
    missing_topics = sorted(set(TOPIC_TERMS) - set(coverage))
    raw_search_urls = {
        url for search in web_backend.searches for url in search["result_urls"]
    }
    allowed_search_urls = sorted(url for url in raw_search_urls if tools.url_allowed(url))
    fetched_pages = {
        page.url: page
        for page in tools.fetched_pages.values()
    }
    final_decision = (
        model_provider.decisions[-1]["result"]
        if model_provider.decisions
        else {}
    )
    usage = tracker.report.model_usage
    return {
        "run_id": tracker.run_id,
        "site": site,
        "model": model,
        "rep": rep,
        "status": "completed",
        "search_queries": [search["query"] for search in web_backend.searches],
        "search_result_urls": allowed_search_urls,
        "fetches": web_backend.fetches,
        "fetched_pages": [
            {
                "url": page.url,
                "title": page.title,
                "scope": page.scope,
            }
            for page in sorted(fetched_pages.values(), key=lambda item: item.url)
        ],
        "selected_urls": [page.url for page in result.selected_pages],
        "selected_titles": [page.title for page in result.selected_pages],
        "topic_coverage": coverage,
        "covered_topics": covered_topics,
        "missing_topics": missing_topics,
        "model_decisions": model_provider.decisions,
        "final_decision": final_decision.get("decision"),
        "model_unanswered_topics": final_decision.get(
            "unanswered_topics",
            result.unanswered_topics,
        ),
        "summary": result.summary,
        "termination_reason": result.termination_reason,
        "discovery_steps": sum(
            not decision["correction"] for decision in model_provider.decisions
        ),
        "performance": {
            "model_calls": usage.requests,
            "input_tokens": usage.input_tokens if usage.usage_available else None,
            "output_tokens": usage.output_tokens if usage.usage_available else None,
            "total_tokens": usage.total_tokens if usage.usage_available else None,
            "latency_sec": tracker.report.duration_seconds,
            "search_calls": len(web_backend.searches),
            "fetch_attempts": len(web_backend.fetches),
            "fetch_successes": sum(
                fetch["status"] == "completed" for fetch in web_backend.fetches
            ),
        },
    }


def jaccard(left: set[str], right: set[str]) -> float:
    """Return Jaccard similarity, treating two empty sets as identical."""

    union = left | right
    return len(left & right) / len(union) if union else 1.0


def write_log(summaries: list[dict[str, Any]]) -> None:
    """Rewrite the compact CSV with rep-one stability comparisons."""

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    baselines: dict[tuple[str, str], dict[str, Any]] = {}
    with RUN_LOG.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for summary in sorted(
            summaries,
            key=lambda item: (item["site"], item["model"], item["rep"]),
        ):
            key = summary["site"], summary["model"]
            baseline = baselines.setdefault(key, summary)
            performance = summary.get("performance", {})
            covered_topics = set(summary.get("covered_topics", []))
            missing_topics = set(TOPIC_TERMS) - covered_topics
            search_similarity = jaccard(
                set(summary.get("search_result_urls", [])),
                set(baseline.get("search_result_urls", [])),
            )
            selected_similarity = jaccard(
                set(summary.get("selected_urls", [])),
                set(baseline.get("selected_urls", [])),
            )
            topic_similarity = jaccard(
                covered_topics,
                set(baseline.get("covered_topics", [])),
            )
            writer.writerow(
                {
                    "run_id": summary["run_id"],
                    "site": summary["site"],
                    "model": summary["model"],
                    "rep": summary["rep"],
                    "status": summary["status"],
                    "decision": summary.get("final_decision", ""),
                    "discovery_steps": summary.get("discovery_steps", ""),
                    "search_calls": performance.get("search_calls", ""),
                    "unique_search_results": len(
                        summary.get("search_result_urls", [])
                    ),
                    "fetch_attempts": performance.get("fetch_attempts", ""),
                    "fetch_successes": performance.get("fetch_successes", ""),
                    "selected_pages": len(summary.get("selected_urls", [])),
                    "coverage_count": len(covered_topics),
                    "coverage_fraction": f"{len(covered_topics) / len(TOPIC_TERMS):.2f}",
                    "covered_topics": "|".join(sorted(covered_topics)),
                    "missing_topics": "|".join(sorted(missing_topics)),
                    "model_unanswered_topics": "|".join(
                        summary.get("model_unanswered_topics", [])
                    ),
                    "search_url_jaccard_to_rep1": f"{search_similarity:.3f}",
                    "selected_url_jaccard_to_rep1": f"{selected_similarity:.3f}",
                    "topic_jaccard_to_rep1": f"{topic_similarity:.3f}",
                    "model_calls": performance.get("model_calls", ""),
                    "input_tokens": performance.get("input_tokens", ""),
                    "output_tokens": performance.get("output_tokens", ""),
                    "total_tokens": performance.get("total_tokens", ""),
                    "latency_sec": (
                        f"{performance['latency_sec']:.3f}"
                        if performance.get("latency_sec") is not None
                        else ""
                    ),
                    "termination_reason": summary.get("termination_reason", ""),
                    "notes": summary.get("error", ""),
                }
            )


def load_existing_summaries() -> list[dict[str, Any]]:
    """Load all compact run summaries already stored in the result directory."""

    if not OUTPUT_ROOT.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for path in sorted(OUTPUT_ROOT.glob("*-rep*.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8")))
    return summaries


def main() -> int:
    """Run requested repetitions serially and refresh the aggregate CSV."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--site", choices=sorted(DEFAULT_INPUTS), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-discovery-steps", type=int, default=2)
    parser.add_argument("--keyword", action="append", default=[])
    parser.add_argument("--measurements", type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1.")
    if args.max_discovery_steps < 1:
        parser.error("--max-discovery-steps must be at least 1.")

    load_dotenv(ROOT / ".env")
    measurement_path = args.measurements or DEFAULT_INPUTS[args.site]
    measurements = load_measurements(measurement_path)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    for rep in range(1, args.repetitions + 1):
        output_path = OUTPUT_ROOT / f"{args.site}-{args.model}-rep{rep}.json"
        if output_path.exists():
            print(f"[skipped] {output_path.name} already exists", flush=True)
            continue
        print(
            f"\n{'=' * 72}\n{args.site} | {args.model} | discovery rep {rep}\n{'=' * 72}",
            flush=True,
        )
        summary = run_one(
            site=args.site,
            model=args.model,
            rep=rep,
            measurements=measurements,
            max_steps=args.max_discovery_steps,
            keywords=args.keyword,
        )
        output_path.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_log(load_existing_summaries())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
