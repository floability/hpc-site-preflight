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
    UnmappedSubmissionOptionFinding,
)
from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import (
    EvidenceLink,
    EvidenceRecord,
    UnresolvedAction,
)
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.evidence.reconciliation import get_rule
from hpc_site_preflight.profiles.models import (
    SiteProfile,
    UnmappedSubmissionOption,
    UnresolvedWorkItem,
)


def apply_documentation(
    profile: SiteProfile,
    report: EvidenceReport,
    documentation: DocumentationEvidence,
) -> tuple[SiteProfile, EvidenceReport]:
    """Apply only reviewed field mappings and append exact documentation evidence."""

    if documentation.site_id != profile.site_id:
        return profile, report

    resolved_paths: set[str] = set()
    mapping_work: list[tuple[str, str]] = []
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
                _link_evidence(report, path, evidence_ids)
                resolved_paths.add(path)
                if isinstance(finding, UnmappedSubmissionOptionFinding):
                    mapping_work.append((path, finding.documented_name))

    profile.unresolved = [item for item in profile.unresolved if item.field not in resolved_paths]
    report.unresolved = [
        item for item in report.unresolved if item.field_path not in resolved_paths
    ]
    for path, documented_name in mapping_work:
        _add_mapping_work(profile, report, path, documented_name)
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
        normalized = finding.name.casefold()
        storage = next(
            (
                item
                for item in profile.storage
                if normalized in {item.id.casefold(), item.name.casefold(), item.role.casefold()}
            ),
            None,
        )
        if storage is not None:
            storage.purge_after_days = finding.purge_after_days
            return [f"/storage/{storage.id}/purge_after_days"]
    if isinstance(finding, SubmissionOptionFinding):
        if finding.requirement not in {"required", "optional"}:
            return []
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
    if isinstance(finding, UnmappedSubmissionOptionFinding):
        if profile.slurm is not None:
            options = profile.slurm.unmapped_options
            path = "/slurm/unmapped_options"
        elif profile.htcondor is not None:
            options = profile.htcondor.unmapped_submit_attributes
            path = "/htcondor/unmapped_submit_attributes"
        else:
            return []
        if not any(
            item.documented_name == finding.documented_name for item in options
        ):
            options.append(
                UnmappedSubmissionOption(
                    documented_name=finding.documented_name,
                    documented_syntax=finding.documented_syntax,
                    requirement=finding.requirement,
                )
            )
        return [path]
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
    if isinstance(finding, UnmappedSubmissionOptionFinding):
        return finding.documented_name
    if isinstance(finding, PartitionFinding):
        return finding.maximum_walltime_seconds
    if isinstance(finding, NetworkFinding):
        return finding.available
    if isinstance(finding, ChargingModelFinding):
        return finding.charging_model
    return finding.purge_after_days


def _link_evidence(
    report: EvidenceReport,
    path: str,
    evidence_ids: list[str],
) -> None:
    """Merge documentation evidence IDs into one link per profile field."""

    report_link = next(
        (item for item in report.links if item.profile_field == path),
        None,
    )
    if report_link is None:
        report.links.append(
            EvidenceLink(profile_field=path, evidence_ids=evidence_ids)
        )
    else:
        report_link.evidence_ids.extend(
            item for item in evidence_ids if item not in report_link.evidence_ids
        )


def _add_mapping_work(
    profile: SiteProfile,
    report: EvidenceReport,
    path: str,
    documented_name: str,
) -> None:
    """Keep an accepted unknown option unresolved until a reviewed mapping exists."""

    reason = f"Documented submission option {documented_name!r} needs a reviewed mapping."
    if not any(item.field == path for item in profile.unresolved):
        profile.unresolved.append(
            UnresolvedWorkItem(
                field=path,
                reason=reason,
                next_action="admin_confirmation",
                action_id="submission_option_mapping",
            )
        )
    if not any(item.field_path == path for item in report.unresolved):
        report.unresolved.append(
            UnresolvedAction(
                field_path=path,
                action="admin_confirmation",
                action_id="submission_option_mapping",
                reason=reason,
            )
        )


def _update_validation(profile: SiteProfile, resolved_paths: set[str]) -> None:
    if any(path.startswith("/slurm/partitions/") for path in resolved_paths):
        profile.section_status.resources = "documented"
    if any(path.startswith("/accounting/") for path in resolved_paths):
        profile.section_status.accounting = "documented"
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
        profile.section_status.submission = (
            "documented"
            if all(option.required is not None for option in options)
            else "partial"
        )
    if any(path.startswith("/storage/") for path in resolved_paths):
        profile.section_status.storage = "partial"
    if any(path.startswith("/network/") for path in resolved_paths):
        profile.section_status.network = "partial"
