"""Deterministically apply validated documentation findings to a profile."""

from hpc_site_preflight.documentation.models import (
    AllocationRequiredFinding,
    ChargingModelFinding,
    DocumentationEvidence,
    DocumentationFinding,
    NetworkFinding,
    PartitionFinding,
    RuntimeMode,
    StoragePolicyFinding,
    SubmissionOptionFinding,
)
from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import EvidenceLink, EvidenceRecord
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.evidence.reconciliation import get_rule
from hpc_site_preflight.profiles.models import FieldEvidenceLink, SiteProfile


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
    if isinstance(finding, AllocationRequiredFinding):
        profile.accounting.allocation_required = finding.allocation_required
        return ["/accounting/allocation_required"]
    if isinstance(finding, ChargingModelFinding):
        profile.accounting.charging_model = finding.charging_model
        return ["/accounting/charging_model"]
    if isinstance(finding, PartitionFinding):
        if profile.slurm is None:
            return []
        partition = next(
            (item for item in profile.slurm.partitions if item.name == finding.name),
            None,
        )
        if partition is not None:
            partition.maximum_walltime_seconds = finding.maximum_walltime_seconds
            return [f"/slurm/partitions/{partition.name}/maximum_walltime_seconds"]
    if isinstance(finding, StoragePolicyFinding):
        storage = next((item for item in profile.storage if item.name == finding.name), None)
        if storage is not None:
            storage.purge_after_days = finding.purge_after_days
            return [f"/storage/{storage.name}/purge_after_days"]
    if isinstance(finding, SubmissionOptionFinding):
        options = (
            profile.slurm.options
            if profile.slurm is not None
            else profile.htcondor.submit_attributes
            if profile.htcondor is not None
            else []
        )
        option = next(
            (item for item in options if item.name == finding.name),
            None,
        )
        if option is not None:
            option.required = finding.requirement == "required"
            prefix = "slurm/options" if profile.slurm is not None else "htcondor/submit_attributes"
            return [f"/{prefix}/{option.name}/required"]
    if isinstance(finding, NetworkFinding):
        if finding.name == "manager_worker":
            profile.network.login_compute.tcp_connect = finding.available
            return ["/network/login_compute/tcp_connect"]
        if finding.name == "worker_worker":
            profile.network.compute_compute.tcp_connect = finding.available
            return ["/network/compute_compute/tcp_connect"]
        profile.network.compute.outbound_https = finding.available
        return ["/network/compute/outbound_https"]
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
                value=_finding_value(finding),
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


def _finding_value(finding: DocumentationFinding) -> bool | int | str:
    if isinstance(finding, AllocationRequiredFinding):
        return finding.allocation_required
    if isinstance(finding, SubmissionOptionFinding):
        return finding.requirement == "required"
    if isinstance(finding, PartitionFinding):
        return finding.maximum_walltime_seconds
    if isinstance(finding, NetworkFinding):
        return finding.available
    if isinstance(finding, ChargingModelFinding):
        return finding.charging_model
    return finding.purge_after_days


def _update_validation(profile: SiteProfile, resolved_paths: set[str]) -> None:
    section_states = {item.section: item for item in profile.validation}
    if any(path.startswith("/slurm/partitions/") for path in resolved_paths):
        section_states["resources"].state = "documented"
    if any(path.startswith("/accounting/") for path in resolved_paths):
        section_states["accounting"].state = "documented"
    if any(
        path.startswith(("/slurm/options/", "/htcondor/submit_attributes/"))
        for path in resolved_paths
    ):
        options = (
            profile.slurm.options
            if profile.slurm is not None
            else profile.htcondor.submit_attributes
            if profile.htcondor is not None
            else []
        )
        section_states["submission"].state = (
            "documented"
            if all(option.required is not None for option in options)
            else "partial"
        )
    if any(path.startswith("/storage/") for path in resolved_paths):
        section_states["storage"].state = "partial"
    if any(path.startswith("/network/") for path in resolved_paths):
        section_states["network"].state = "partial"
