"""Small data contracts shared by the documentation pipeline."""

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from hpc_site_preflight.providers.base import ModelProviderName

DocumentationScope = Literal[
    "target_site", "organization_general", "sibling_site", "out_of_scope"
]
ContextMode = Literal["full-corpus", "bm25", "schema-expanded-bm25"]
RuntimeMode = Literal["live", "simulate"]
ExtractionGroupName = Literal["submission", "network", "operational"]
BlockKind = Literal["text", "table"]
DocumentationScalar: TypeAlias = str | int | float | bool
DocumentationValue: TypeAlias = DocumentationScalar | list[DocumentationScalar]


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


class EvidenceSpan(StrictModel):
    span_id: str
    chunk_id: str
    source_url: str
    title: str
    heading: str
    scope: DocumentationScope
    quote: str


class ExtractionCandidate(StrictModel):
    field: str
    resource: str | None
    value: DocumentationValue | None
    evidence_span_ids: list[str]
    note: str


class ExtractionResult(StrictModel):
    findings: list[ExtractionCandidate]


class DocumentationCitation(StrictModel):
    span_id: str
    chunk_id: str
    url: str
    title: str
    heading: str
    quote: str


class DocumentationFinding(StrictModel):
    field: str
    resource: str | None
    value: DocumentationValue
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class DocumentationEvidence(StrictModel):
    site_id: str
    model_mode: RuntimeMode
    model_provider: ModelProviderName
    model: str | None
    web_mode: RuntimeMode
    context_mode: ContextMode
    findings: list[DocumentationFinding]
    rejected: list[str]
    unresolved: list[str]
    selected_chunk_ids: list[str]
    retrieval: list[FieldRetrieval]
