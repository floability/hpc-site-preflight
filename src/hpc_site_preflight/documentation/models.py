"""Small data contracts shared by the documentation pipeline."""

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hpc_site_preflight.providers.base import ModelProviderName

DocumentationScope = Literal[
    "target_site", "organization_general", "sibling_site", "out_of_scope"
]
ContextMode = Literal["full-corpus", "bm25", "llm-expanded-bm25"]
RuntimeMode = Literal["live", "simulate"]
ExtractionGroupName = Literal["submission", "network", "operational"]
BlockKind = Literal["text", "table"]
SubmissionRequirement = Literal["required", "recommended", "optional", "conditional"]
NetworkCapabilityName = Literal["manager_worker", "worker_worker", "outbound_compute"]
HTCondorPolicyName = Literal["guaranteed_runtime", "preemptible"]
SubmissionOptionName = Literal[
    "account",
    "partition",
    "nodes",
    "cpus-per-task",
    "time",
    "request_cpus",
    "request_memory",
    "request_gpus",
    "universe",
    "executable",
    "should_transfer_files",
    "when_to_transfer_output",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteIdentity(StrictModel):
    site_id: str
    site_name: str
    aliases: list[str]
    scheduler: Literal["slurm", "htcondor", "unknown"]
    hostname_patterns: list[str]
    observed_hosts: list[str]
    allowed_domains: list[str]
    preferred_path_tokens: list[str]
    discovery_site_name: str | None = None
    discovery_note: str | None = None
    discovery_keywords: list[str] = Field(default_factory=list)


class SearchQuery(StrictModel):
    topic: Literal["canonical", "submission", "resources", "storage", "networking", "user"]
    query: str


class QueryPlan(StrictModel):
    site_id: str
    queries: list[SearchQuery]


class SearchResult(StrictModel):
    url: str
    title: str
    snippet: str


class DocumentBlock(StrictModel):
    kind: BlockKind
    text: str


class DocumentSection(StrictModel):
    heading_path: list[str] = Field(min_length=1)
    blocks: list[DocumentBlock] = Field(min_length=1)


class DocumentLink(StrictModel):
    url: str
    text: str


class RecordedPage(StrictModel):
    url: str
    title: str
    fetched_at: datetime
    sections: list[DocumentSection] = Field(min_length=1)
    links: list[DocumentLink] = Field(default_factory=list)


class WebRecording(StrictModel):
    schema_version: Literal["0.1"]
    note: str
    search_results: list[SearchResult]
    pages: list[RecordedPage]


class FetchedPage(RecordedPage):
    scope: DocumentationScope
    content_hash: str
    text_truncated: bool = False


class DiscoverySelection(StrictModel):
    source_urls: list[str] = Field(max_length=10)
    decision: Literal["complete", "search_more"]
    follow_up_queries: list[str] = Field(max_length=3)
    summary: str = Field(min_length=1)
    unanswered_topics: list[str]


class DiscoveryResult(StrictModel):
    selected_pages: list[FetchedPage]
    summary: str
    unanswered_topics: list[str]
    termination_reason: Literal["model_selected", "model_corrected", "deterministic_fallback"]


class CorpusDocument(StrictModel):
    document_id: str
    source_url: str
    title: str
    scope: DocumentationScope
    content_hash: str
    fetched_at: datetime


class CorpusChunk(StrictModel):
    chunk_id: str
    document_id: str
    source_url: str
    title: str
    scope: DocumentationScope
    heading_path: list[str]
    block_kind: BlockKind
    text: str
    content_hash: str


class CorpusManifest(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    site_id: str
    fingerprint: str
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)


class RetrievalHit(StrictModel):
    chunk_id: str
    score: float | None
    cited: bool = False


class FieldRetrieval(StrictModel):
    field: str
    queries: list[str]
    hits: list[RetrievalHit]


class ContextSelection(StrictModel):
    group: ExtractionGroupName
    mode: ContextMode
    chunks: list[CorpusChunk]
    selected_chunk_ids: list[str]
    retrievals: list[FieldRetrieval]


class ExpandedRetrievalQuery(StrictModel):
    field: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    query: str = Field(min_length=3, max_length=240)


class QueryExpansionResult(StrictModel):
    queries: list[ExpandedRetrievalQuery] = Field(max_length=16)


class EvidenceSpan(StrictModel):
    span_id: str
    chunk_id: str
    source_url: str
    title: str
    heading: str
    scope: DocumentationScope
    quote: str


class ExtractedBoolean(StrictModel):
    value: bool = Field(strict=True)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedString(StrictModel):
    value: str = Field(min_length=1, strict=True)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedSubmissionOption(StrictModel):
    name: SubmissionOptionName
    requirement: SubmissionRequirement
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedUnmappedSubmissionOption(StrictModel):
    documented_name: str = Field(min_length=1, strict=True)
    documented_syntax: list[str]
    requirement: SubmissionRequirement
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedPartition(StrictModel):
    name: str = Field(strict=True)
    maximum_walltime_seconds: int = Field(ge=0, strict=True)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class SubmissionExtractionResult(StrictModel):
    allocation_required: ExtractedBoolean | None
    guaranteed_runtime: ExtractedBoolean | None
    preemptible: ExtractedBoolean | None
    submission_options: list[ExtractedSubmissionOption]
    unmapped_options: list[ExtractedUnmappedSubmissionOption]
    partitions: list[ExtractedPartition]

    @model_validator(mode="before")
    @classmethod
    def migrate_runtime_policy_fields(cls, value: object) -> object:
        """Treat missing runtime fields in older recordings as documentation silence."""

        if isinstance(value, dict):
            return {
                "guaranteed_runtime": None,
                "preemptible": None,
                **value,
            }
        return value


class ExtractedNetworkCapability(StrictModel):
    name: NetworkCapabilityName
    available: bool = Field(strict=True)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class NetworkExtractionResult(StrictModel):
    network: list[ExtractedNetworkCapability]


class ExtractedStoragePolicy(StrictModel):
    name: str = Field(strict=True)
    purge_after_days: int = Field(ge=0, strict=True)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class OperationalExtractionResult(StrictModel):
    charging_model: ExtractedString | None
    storage: list[ExtractedStoragePolicy]


class FullCorpusExtractionResult(StrictModel):
    submission: SubmissionExtractionResult
    network: NetworkExtractionResult
    operational: OperationalExtractionResult


class DocumentationCitation(StrictModel):
    span_id: str
    chunk_id: str
    url: str
    title: str
    heading: str
    quote: str


class AllocationRequiredFinding(StrictModel):
    allocation_required: bool
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class SubmissionOptionFinding(StrictModel):
    name: SubmissionOptionName
    requirement: SubmissionRequirement
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class UnmappedSubmissionOptionFinding(StrictModel):
    documented_name: str
    documented_syntax: list[str]
    requirement: SubmissionRequirement
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class PartitionFinding(StrictModel):
    name: str
    maximum_walltime_seconds: int = Field(ge=0)
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class NetworkFinding(StrictModel):
    name: NetworkCapabilityName
    available: bool
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class ChargingModelFinding(StrictModel):
    charging_model: str = Field(min_length=1)
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class HTCondorPolicyFinding(StrictModel):
    name: HTCondorPolicyName
    value: bool
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class StoragePolicyFinding(StrictModel):
    name: str
    purge_after_days: int = Field(ge=0)
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


DocumentationFinding: TypeAlias = (
    AllocationRequiredFinding
    | SubmissionOptionFinding
    | UnmappedSubmissionOptionFinding
    | PartitionFinding
    | NetworkFinding
    | ChargingModelFinding
    | HTCondorPolicyFinding
    | StoragePolicyFinding
)


class DocumentationEvidence(StrictModel):
    site_id: str
    model_mode: RuntimeMode
    model_provider: ModelProviderName
    model: str | None
    web_mode: RuntimeMode
    context_mode: ContextMode
    corpus_fingerprint: str | None = None
    findings: list[DocumentationFinding]
    rejected: list[str]
    unresolved: list[str]
    selected_chunk_ids: list[str]
    retrieval: list[FieldRetrieval]
