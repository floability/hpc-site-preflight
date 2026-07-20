"""Deterministically apply validated documentation findings to a profile."""

from hpc_site_preflight.documentation.models import (
    DocumentationEvidence,
    DocumentationFinding,
    RuntimeMode,
)
from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import EvidenceLink, EvidenceRecord
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.evidence.reconciliation import get_rule
from hpc_site_preflight.profiles.models import FieldEvidenceLink, SiteProfile

_NETWORK_FIELDS = {
    "manager_worker_connectivity": "manager_worker",
    "worker_worker_connectivity": "worker_worker",
    "outbound_compute": "outbound_compute",
}


def apply_documentation(
    profile: SiteProfile,
    report: EvidenceReport,
    documentation: DocumentationEvidence,
) -> tuple[SiteProfile, EvidenceReport]:
    """Apply only reviewed field mappings and append exact documentation evidence."""

    if documentation.site_id != profile.site_id:
        return profile, report

    resolved_paths: set[str] = set()
    for finding in documentation.findings:
        paths = _apply_finding(profile, finding)
        for path in paths:
            rule = get_rule(path)
            if rule is None or "documentation" not in rule.allowed_sources:
                continue
            evidence_ids = _append_evidence(
                report,
                profile.site_id,
                path,
                finding,
                documentation.web_mode,
            )
            if evidence_ids:
                profile.field_evidence.append(
                    FieldEvidenceLink(field=path, evidence_ids=evidence_ids)
                )
                report.links.append(EvidenceLink(profile_field=path, evidence_ids=evidence_ids))
                resolved_paths.add(path)

    profile.unresolved = [item for item in profile.unresolved if item.field not in resolved_paths]
    report.unresolved = [
        item for item in report.unresolved if item.field_path not in resolved_paths
    ]
    _update_validation(profile, resolved_paths)
    return profile, report


def _apply_finding(profile: SiteProfile, finding: DocumentationFinding) -> list[str]:
    value = finding.value
    if finding.field == "allocation_required" and isinstance(value, bool):
        profile.accounting.allocation_required = value
        return ["/accounting/allocation_required"]
    if finding.field == "charging_model" and isinstance(value, str):
        profile.accounting.charging_model = value
        return ["/accounting/charging_model"]
    if finding.field == "maximum_walltime_seconds" and isinstance(value, int):
        partition = next(
            (item for item in profile.partitions if item.name == finding.resource),
            None,
        )
        if partition is not None:
            partition.maximum_walltime_seconds = value
            return [f"/partitions/{partition.name}/maximum_walltime_seconds"]
    if finding.field == "purge_after_days" and isinstance(value, int):
        storage = next((item for item in profile.storage if item.name == finding.resource), None)
        if storage is not None:
            storage.purge_after_days = value
            return [f"/storage/{storage.name}/purge_after_days"]
    if finding.field == "required_submission_options" and isinstance(value, list):
        names = {item for item in value if isinstance(item, str)}
        paths: list[str] = []
        for option in profile.submission_options:
            if option.name in names:
                option.requirement = "required"
                paths.append(f"/submission_options/{option.name}/requirement")
        return paths
    if finding.field in _NETWORK_FIELDS and isinstance(value, bool):
        capability_name = _NETWORK_FIELDS[finding.field]
        capability = next(item for item in profile.network if item.name == capability_name)
        capability.available = value
        return [f"/network/{capability_name}"]
    return []


def _append_evidence(
    report: EvidenceReport,
    site_id: str,
    field_path: str,
    finding: DocumentationFinding,
    web_mode: RuntimeMode,
) -> list[str]:
    evidence_ids: list[str] = []
    for citation in finding.citations:
        evidence_id = build_evidence_id(
            "documentation",
            site_id,
            field_path,
            f"{citation.url}#{citation.span_id}",
        )
        report.evidence.append(
            EvidenceRecord(
                evidence_id=evidence_id,
                field_path=field_path,
                source_type="documentation",
                scope="target_site",
                trust="official" if web_mode == "live" else "illustrative",
                disposition="accepted",
                value=finding.value,
                freshness="site_change",
                source_reference=citation.url,
                documentation_url=citation.url,
                documentation_heading=citation.heading,
                exact_quote=citation.quote,
                chunk_id=citation.chunk_id,
            )
        )
        evidence_ids.append(evidence_id)
    return evidence_ids


def _update_validation(profile: SiteProfile, resolved_paths: set[str]) -> None:
    section_states = {item.section: item for item in profile.validation}
    if any(path.startswith("/partitions/") for path in resolved_paths):
        section_states["resources"].state = "documented"
    if any(path.startswith("/accounting/") for path in resolved_paths):
        section_states["accounting"].state = "documented"
    if any(path.startswith("/storage/") for path in resolved_paths):
        section_states["storage"].state = "partial"
    network_paths = {f"/network/{name}" for name in _NETWORK_FIELDS.values()}
    if network_paths <= resolved_paths:
        section_states["network"].state = "documented"
