"""Small data contracts shared by the documentation pipeline."""

from datetime import datetime
from typing import Literal, Self, TypeAlias

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
SubmissionSupport = Literal["supported", "unsupported", "discouraged", "unknown"]
NetworkCapabilityName = Literal["manager_worker", "worker_worker", "outbound_compute"]
HTCondorPolicyName = Literal[
    "guaranteed_runtime",
    "preemptible",
    "submission_host",
    "machine_requirements_supported",
    "dynamic_slots_enabled",
    "bulk_submission_supported",
    "completion_email_supported",
]
HTCondorPolicyValue: TypeAlias = str | bool
SubmissionOptionName = str
AccountingPolicyName = Literal[
    "charging_unit",
    "charging_model",
    "filesystem_storage_charged",
]
StoragePolicyName = Literal[
    "compute_visible",
    "compute_readable",
    "compute_writable",
    "shared_across_compute_nodes",
    "backup_policy",
    "purge_after_days",
    "purge_condition",
]
StoragePolicyValue: TypeAlias = str | int | bool
PartitionPolicyName = Literal[
    "maximum_walltime_seconds",
    "maximum_nodes_per_job",
    "shared_nodes",
    "gpu_count_per_node",
    "gpu_models",
    "features",
]
PartitionPolicyValue: TypeAlias = int | bool | list[str]


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


class ExtractedInteger(StrictModel):
    value: int = Field(strict=True)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedStringList(StrictModel):
    value: list[str] = Field(min_length=1)
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


ExtractedPartitionValue: TypeAlias = (
    ExtractedInteger | ExtractedBoolean | ExtractedStringList
)
ExtractedStorageValue: TypeAlias = ExtractedInteger | ExtractedBoolean | ExtractedString


class ExtractedSubmissionOption(StrictModel):
    name: SubmissionOptionName
    syntax: list[str]
    requirement: SubmissionRequirement
    support: SubmissionSupport
    condition: str | None
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedUnmappedSubmissionOption(StrictModel):
    documented_name: str = Field(min_length=1, strict=True)
    documented_syntax: list[str]
    requirement: SubmissionRequirement
    support: SubmissionSupport
    condition: str | None
    evidence_span_ids: list[str] = Field(min_length=1)
    note: str


class ExtractedPartition(StrictModel):
    name: str = Field(strict=True)
    maximum_walltime_seconds: ExtractedInteger | None
    maximum_nodes_per_job: ExtractedInteger | None
    shared_nodes: ExtractedBoolean | None
    gpu_count_per_node: ExtractedInteger | None
    gpu_models: ExtractedStringList | None
    features: ExtractedStringList | None

    @model_validator(mode="after")
    def validate_resource_values(self) -> Self:
        """Apply numeric bounds that differ among partition fields."""

        if (
            self.maximum_walltime_seconds is not None
            and self.maximum_walltime_seconds.value < -1
        ):
            raise ValueError("maximum_walltime_seconds must be -1 or nonnegative")
        for field in ("maximum_nodes_per_job", "gpu_count_per_node"):
            proposal = getattr(self, field)
            if proposal is not None and proposal.value < 1:
                raise ValueError(f"{field} must be positive")
        return self


class SubmissionExtractionResult(StrictModel):
    allocation_required: ExtractedBoolean | None
    guaranteed_runtime: ExtractedBoolean | None
    preemptible: ExtractedBoolean | None
    submission_host: ExtractedString | None
    machine_requirements_supported: ExtractedBoolean | None
    dynamic_slots_enabled: ExtractedBoolean | None
    bulk_submission_supported: ExtractedBoolean | None
    completion_email_supported: ExtractedBoolean | None
    submission_options: list[ExtractedSubmissionOption]
    unmapped_options: list[ExtractedUnmappedSubmissionOption]
    partitions: list[ExtractedPartition]

    @model_validator(mode="before")
    @classmethod
    def migrate_runtime_policy_fields(cls, value: object) -> object:
        """Treat missing runtime fields in older recordings as documentation silence."""

        if isinstance(value, dict):
            submission_options = []
            for option in value.get("submission_options", []):
                if isinstance(option, dict):
                    option = {
                        "syntax": [],
                        "support": "unknown",
                        "condition": None,
                        **option,
                    }
                submission_options.append(option)
            unmapped_options = []
            for option in value.get("unmapped_options", []):
                if isinstance(option, dict):
                    option = {
                        "support": "unknown",
                        "condition": None,
                        **option,
                    }
                unmapped_options.append(option)
            partitions = []
            for partition in value.get("partitions", []):
                if isinstance(partition, dict):
                    partition = _migrate_partition_proposal(partition)
                partitions.append(partition)
            return {
                "guaranteed_runtime": None,
                "preemptible": None,
                "submission_host": None,
                "machine_requirements_supported": None,
                "dynamic_slots_enabled": None,
                "bulk_submission_supported": None,
                "completion_email_supported": None,
                **value,
                "submission_options": submission_options,
                "unmapped_options": unmapped_options,
                "partitions": partitions,
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
    compute_visible: ExtractedBoolean | None
    compute_readable: ExtractedBoolean | None
    compute_writable: ExtractedBoolean | None
    shared_across_compute_nodes: ExtractedBoolean | None
    backup_policy: ExtractedString | None
    purge_after_days: ExtractedInteger | None
    purge_condition: ExtractedString | None

    @model_validator(mode="after")
    def validate_purge_days(self) -> Self:
        """Reject negative documented retention periods."""

        if self.purge_after_days is not None and self.purge_after_days.value < 0:
            raise ValueError("purge_after_days must be nonnegative")
        return self


class OperationalExtractionResult(StrictModel):
    charging_unit: ExtractedString | None
    charging_model: ExtractedString | None
    filesystem_storage_charged: ExtractedBoolean | None
    storage: list[ExtractedStoragePolicy]

    @model_validator(mode="before")
    @classmethod
    def migrate_operational_fields(cls, value: object) -> object:
        """Treat fields missing from older recordings as documentation silence."""

        if not isinstance(value, dict):
            return value
        storage = []
        for item in value.get("storage", []):
            if isinstance(item, dict):
                item = _migrate_storage_proposal(item)
            storage.append(item)
        return {
            "charging_unit": None,
            "filesystem_storage_charged": None,
            **value,
            "storage": storage,
        }


def _migrate_partition_proposal(value: dict[str, object]) -> dict[str, object]:
    """Wrap scalar fields from older recordings in field-local evidence objects."""

    migrated = dict(value)
    span_ids = migrated.pop("evidence_span_ids", [])
    note = migrated.pop("note", "")
    fields = (
        "maximum_walltime_seconds",
        "maximum_nodes_per_job",
        "shared_nodes",
        "gpu_count_per_node",
        "gpu_models",
        "features",
    )
    for field in fields:
        proposal = migrated.get(field)
        if proposal in (None, []):
            migrated[field] = None
        elif not isinstance(proposal, dict):
            migrated[field] = {
                "value": proposal,
                "evidence_span_ids": span_ids,
                "note": note,
            }
    return {field: None for field in fields} | migrated


def _migrate_storage_proposal(value: dict[str, object]) -> dict[str, object]:
    """Wrap scalar storage fields from older recordings in local evidence objects."""

    migrated = dict(value)
    span_ids = migrated.pop("evidence_span_ids", [])
    note = migrated.pop("note", "")
    fields = (
        "compute_visible",
        "compute_readable",
        "compute_writable",
        "shared_across_compute_nodes",
        "backup_policy",
        "purge_after_days",
        "purge_condition",
    )
    for field in fields:
        proposal = migrated.get(field)
        if proposal is None:
            migrated[field] = None
        elif not isinstance(proposal, dict):
            migrated[field] = {
                "value": proposal,
                "evidence_span_ids": span_ids,
                "note": note,
            }
    return {field: None for field in fields} | migrated


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
    syntax: list[str] = Field(default_factory=list)
    requirement: SubmissionRequirement
    support: SubmissionSupport = "unknown"
    condition: str | None = None
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class UnmappedSubmissionOptionFinding(StrictModel):
    documented_name: str
    documented_syntax: list[str]
    requirement: SubmissionRequirement
    support: SubmissionSupport = "unknown"
    condition: str | None = None
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class PartitionFinding(StrictModel):
    name: str
    field: PartitionPolicyName
    value: PartitionPolicyValue
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_walltime_finding(cls, value: object) -> object:
        """Read walltime findings recorded before partition fields were generalized."""

        if isinstance(value, dict) and "maximum_walltime_seconds" in value:
            migrated = dict(value)
            walltime = migrated.pop("maximum_walltime_seconds")
            return {
                "field": "maximum_walltime_seconds",
                "value": walltime,
                **migrated,
            }
        return value

    @model_validator(mode="after")
    def validate_field_value(self) -> Self:
        """Require the value type associated with the documented partition field."""

        if self.field == "shared_nodes":
            valid = isinstance(self.value, bool)
        elif self.field in {"gpu_models", "features"}:
            valid = isinstance(self.value, list) and all(
                isinstance(item, str) for item in self.value
            )
        else:
            valid = isinstance(self.value, int) and not isinstance(self.value, bool)
        if not valid:
            raise ValueError(f"invalid value type for partition field {self.field}")
        return self


class NetworkFinding(StrictModel):
    name: NetworkCapabilityName
    available: bool
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class ChargingModelFinding(StrictModel):
    charging_model: str = Field(min_length=1)
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class AccountingPolicyFinding(StrictModel):
    name: AccountingPolicyName
    value: str | bool
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class HTCondorPolicyFinding(StrictModel):
    name: HTCondorPolicyName
    value: HTCondorPolicyValue
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)


class StoragePolicyFinding(StrictModel):
    name: str
    field: StoragePolicyName
    value: StoragePolicyValue
    note: str
    citations: list[DocumentationCitation] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def migrate_purge_finding(cls, value: object) -> object:
        """Read purge findings recorded before storage fields were generalized."""

        if isinstance(value, dict) and "purge_after_days" in value:
            migrated = dict(value)
            purge = migrated.pop("purge_after_days")
            return {"field": "purge_after_days", "value": purge, **migrated}
        return value


DocumentationFinding: TypeAlias = (
    AllocationRequiredFinding
    | SubmissionOptionFinding
    | UnmappedSubmissionOptionFinding
    | PartitionFinding
    | NetworkFinding
    | ChargingModelFinding
    | AccountingPolicyFinding
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
