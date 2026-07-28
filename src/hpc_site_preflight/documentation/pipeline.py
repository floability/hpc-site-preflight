"""Linear orchestration for bounded discovery and extraction."""

from pathlib import Path

from hpc_site_preflight.documentation.corpus import build_corpus, load_corpus, write_corpus
from hpc_site_preflight.documentation.discovery_agent import DiscoveryAgent
from hpc_site_preflight.documentation.extraction import extract_documentation
from hpc_site_preflight.documentation.identity import build_query_plan, build_site_identity
from hpc_site_preflight.documentation.models import (
    ContextMode,
    DocumentationEvidence,
    RuntimeMode,
)
from hpc_site_preflight.documentation.tools import (
    DEFAULT_PAGE_BUDGET,
    DEFAULT_SEARCH_BUDGET,
    FOLLOW_UP_PAGE_BUDGET,
    FOLLOW_UP_SEARCH_BUDGET,
    DocumentationTools,
    WebBackend,
)
from hpc_site_preflight.exceptions import DocumentationError
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.providers.base import ModelProvider, ModelProviderName
from hpc_site_preflight.reporting.tracker import RunTracker


class DocumentationPipeline:
    """Run documentation discovery, corpus construction, and extraction."""

    def __init__(
        self,
        *,
        measurements: MeasurementBundle,
        model_provider: ModelProvider,
        web_backend: WebBackend | None,
        corpus_directory: Path,
        model_mode: RuntimeMode,
        model_provider_name: ModelProviderName,
        model: str | None,
        web_mode: RuntimeMode,
        discovery_site_name: str | None = None,
        discovery_note: str | None = None,
        discovery_keywords: list[str] | None = None,
        max_discovery_steps: int = 2,
        corpus_input: Path | None = None,
    ) -> None:
        self.measurements = measurements
        self.model_provider = model_provider
        self.web_backend = web_backend
        self.corpus_directory = corpus_directory
        self.model_mode = model_mode
        self.model_provider_name = model_provider_name
        self.model = model
        self.web_mode = web_mode
        self.discovery_site_name = discovery_site_name
        self.discovery_note = discovery_note
        self.discovery_keywords = discovery_keywords or []
        self.max_discovery_steps = max_discovery_steps
        self.corpus_input = corpus_input

    def build(
        self,
        tracker: RunTracker,
        *,
        context_mode: ContextMode,
    ) -> DocumentationEvidence:
        site = self.measurements.site_facts
        if self.corpus_input is not None:
            tracker.progress(f"Loading frozen corpus from {self.corpus_input}")
            with tracker.stage("documentation_corpus_load", display=False):
                manifest, documents, chunks = load_corpus(
                    self.corpus_input,
                    expected_site_id=site.site_id,
                )
                tracker.progress(
                    f"Frozen corpus {manifest.fingerprint[:12]} contains "
                    f"{len(documents)} document(s) and {len(chunks)} chunk(s)"
                )
                for name in ("manifest.json", "documents.jsonl", "chunks.jsonl"):
                    tracker.add_artifact(
                        kind="documentation_corpus_input",
                        path=self.corpus_input / name,
                    )
        else:
            if self.web_backend is None:
                raise DocumentationError(
                    "A web backend is required when corpus_input is absent."
                )
            with tracker.stage("documentation_identity"):
                identity = build_site_identity(
                    self.measurements,
                    discovery_site_name=self.discovery_site_name,
                    discovery_note=self.discovery_note,
                    discovery_keywords=self.discovery_keywords,
                )
                query_plan = build_query_plan(identity)

            follow_up_steps = self.max_discovery_steps - 1
            tools = DocumentationTools(
                identity,
                self.web_backend,
                search_budget=DEFAULT_SEARCH_BUDGET
                + FOLLOW_UP_SEARCH_BUDGET * follow_up_steps,
                page_budget=DEFAULT_PAGE_BUDGET + FOLLOW_UP_PAGE_BUDGET * follow_up_steps,
            )
            discovery = DiscoveryAgent(
                self.model_provider,
                max_steps=self.max_discovery_steps,
            ).run(
                identity,
                query_plan,
                tools,
                tracker,
            )

            tracker.progress(
                f"Building corpus from {len(discovery.selected_pages)} selected page(s)"
            )
            with tracker.stage("documentation_corpus", display=False):
                manifest, documents, chunks = build_corpus(
                    site.site_id, discovery.selected_pages
                )
                tracker.progress(
                    f"Corpus contains {len(documents)} document(s) and {len(chunks)} chunk(s)"
                )
                paths = write_corpus(self.corpus_directory, manifest, documents, chunks)
                for path in paths:
                    tracker.add_artifact(kind="documentation_corpus", path=path)

        return extract_documentation(
            site_id=site.site_id,
            site_name=site.site_name,
            scheduler=self.measurements.scheduler_type,
            partition_names=self.measurements.partition_names,
            storage_names=self.measurements.storage_names,
            chunks=chunks,
            context_mode=context_mode,
            model_mode=self.model_mode,
            model_provider=self.model_provider_name,
            model=self.model,
            web_mode=self.web_mode,
            provider=self.model_provider,
            tracker=tracker,
            corpus_fingerprint=manifest.fingerprint,
        )
