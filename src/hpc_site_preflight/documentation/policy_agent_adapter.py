"""Linear orchestration for bounded discovery and extraction."""

from pathlib import Path

from hpc_site_preflight.documentation.base import DocumentationPolicyProvider
from hpc_site_preflight.documentation.corpus import build_corpus, write_corpus
from hpc_site_preflight.documentation.discovery import DiscoveryAgent
from hpc_site_preflight.documentation.extraction import extract_documentation
from hpc_site_preflight.documentation.identity import build_query_plan, build_site_identity
from hpc_site_preflight.documentation.models import ContextMode, DocumentationEvidence
from hpc_site_preflight.documentation.web import DocumentationTools, WebBackend
from hpc_site_preflight.measurements.base import MeasurementBundle
from hpc_site_preflight.providers.base import ModelProvider
from hpc_site_preflight.reporting.tracker import RunTracker
from hpc_site_preflight.site_info.models import SiteInfo


class PolicyAgentAdapter(DocumentationPolicyProvider):
    """Connect the small Phase D components without embedding policy decisions."""

    def __init__(
        self,
        *,
        measurements: MeasurementBundle,
        model_provider: ModelProvider,
        web_backend: WebBackend,
        corpus_directory: Path,
        maximum_discovery_turns: int = 8,
    ) -> None:
        self.measurements = measurements
        self.model_provider = model_provider
        self.web_backend = web_backend
        self.corpus_directory = corpus_directory
        self.maximum_discovery_turns = maximum_discovery_turns

    def build(
        self,
        site: SiteInfo,
        tracker: RunTracker,
        *,
        context_mode: ContextMode,
    ) -> DocumentationEvidence:
        with tracker.stage("documentation_identity"):
            identity = build_site_identity(site, self.measurements)
            query_plan = build_query_plan(identity)

        tools = DocumentationTools(identity, self.web_backend)
        discovery = DiscoveryAgent(
            self.model_provider,
            maximum_turns=self.maximum_discovery_turns,
        ).run(identity, query_plan, tools, tracker)

        with tracker.stage("documentation_corpus"):
            manifest, documents, chunks = build_corpus(site.site_id, discovery.selected_pages)
            paths = write_corpus(self.corpus_directory, manifest, documents, chunks)
            for path in paths:
                tracker.add_artifact(kind="documentation_corpus", path=path)

        return extract_documentation(
            site_id=site.site_id,
            site_name=site.site_name,
            scheduler=site.scheduler,
            partition_names=_partition_names(self.measurements),
            storage_names=_storage_names(self.measurements),
            chunks=chunks,
            context_mode=context_mode,
            provider=self.model_provider,
            tracker=tracker,
        )


def _storage_names(measurements: MeasurementBundle) -> set[str]:
    names: set[str] = set()
    prefix = "/facts/storage/filesystems/"
    suffix = "/path"
    for observation in measurements.common:
        if observation.path.startswith(prefix) and observation.path.endswith(suffix):
            names.add(observation.path.removeprefix(prefix).removesuffix(suffix))
    return names


def _partition_names(measurements: MeasurementBundle) -> set[str]:
    for observation in measurements.scheduler:
        if observation.path == "/facts/scheduler/partitions" and isinstance(
            observation.value, list
        ):
            return {item for item in observation.value if isinstance(item, str)}
    return set()
