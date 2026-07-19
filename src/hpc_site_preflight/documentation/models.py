"""Small data contracts shared by the documentation pipeline."""

from datetime import datetime
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocumentationScope = Literal[
    "target_site", "organization_general", "sibling_site", "out_of_scope"
]
ContextMode = Literal["full-corpus", "bm25", "schema-expanded-bm25"]
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


class SearchQuery(StrictModel):
    topic: Literal["submission", "resources", "storage", "networking"]
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


class RecordedPage(StrictModel):
    url: str
    title: str
    fetched_at: datetime
    sections: list[DocumentSection] = Field(min_length=1)


class WebRecording(StrictModel):
    schema_version: Literal["0.1"]
    note: str
    search_results: list[SearchResult]
    pages: list[RecordedPage]


class FetchedPage(RecordedPage):
    scope: DocumentationScope
    content_hash: str
    text_truncated: bool = False


class DiscoveryDecision(StrictModel):
    action: Literal["search_web", "fetch_page", "finish_discovery"]
    query: str | None
    url: str | None
    source_urls: list[str]
    summary: str | None
    unanswered_topics: list[str]

    @model_validator(mode="after")
    def validate_action_arguments(self) -> "DiscoveryDecision":
        if self.action == "search_web" and not self.query:
            raise ValueError("search_web requires query.")
        if self.action == "fetch_page" and not self.url:
            raise ValueError("fetch_page requires url.")
        if self.action == "finish_discovery" and not self.summary:
            raise ValueError("finish_discovery requires summary.")
        return self


class DiscoveryResult(StrictModel):
    selected_pages: list[FetchedPage]
    summary: str
    unanswered_topics: list[str]
    termination_reason: Literal["model_finished", "turn_limit", "model_failed"]


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


class ContextSelection(StrictModel):
    group: ExtractionGroupName
    mode: ContextMode
    query: str
    chunks: list[CorpusChunk]
    selected_chunk_ids: list[str]


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
    context_mode: ContextMode
    findings: list[DocumentationFinding]
    rejected: list[str]
    unresolved: list[str]
    selected_chunk_ids: list[str]
