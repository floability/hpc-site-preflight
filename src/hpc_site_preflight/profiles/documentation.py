"""Deterministically apply validated documentation findings to a profile."""

from typing import cast

from hpc_site_preflight.documentation.models import (
    AccountingPolicyFinding,
    AllocationRequiredFinding,
    ChargingModelFinding,
    DocumentationEvidence,
    DocumentationFinding,
    HTCondorPolicyFinding,
    NetworkFinding,
    PartitionFinding,
    RuntimeMode,
    StoragePolicyFinding,
    SubmissionOptionFinding,
    UnmappedSubmissionOptionFinding,
)
from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import (
    ConflictRecord,
    EvidenceLink,
    EvidenceRecord,
    UnresolvedAction,
)
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.evidence.reconciliation import get_rule
from hpc_site_preflight.profiles.models import (
    ConflictEvidenceValue,
    ProfileConflict,
    ProfileValue,
    SiteProfile,
    SubmissionOption,
    UnmappedSubmissionOption,
    UnresolvedWorkItem,
    canonical_slurm_syntax,
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
                if (
                    isinstance(finding, PartitionFinding)
                    and finding.field == "maximum_walltime_seconds"
                    and isinstance(finding.value, int)
                    and not isinstance(finding.value, bool)
                ):
                    _retain_walltime_conflict(
                        profile,
                        report,
                        path,
                        finding.value,
                        evidence_ids,
                    )
                elif isinstance(finding, PartitionFinding):
                    _retain_measured_partition_conflict(
                        profile,
                        report,
                        path,
                        finding.value,
                        evidence_ids,
                    )
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
    if isinstance(finding, AccountingPolicyFinding):
        setattr(profile.accounting, finding.name, finding.value)
        return [f"/accounting/{finding.name}"]
    if isinstance(finding, HTCondorPolicyFinding):
        if profile.htcondor is None:
            return []
        setattr(profile.htcondor, finding.name, finding.value)
        return [f"/htcondor/{finding.name}"]
    if isinstance(finding, PartitionFinding):
        if profile.slurm is None:
            return []
        partition = next(
            (item for item in profile.slurm.partitions if item.name == finding.name),
            None,
        )
        if partition is not None:
            current = getattr(partition, finding.field)
            if (
                finding.field == "maximum_walltime_seconds"
                or current is None
                or current == []
            ):
                setattr(partition, finding.field, finding.value)
            return [f"/slurm/partitions/{partition.name}/{finding.field}"]
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
            setattr(storage, finding.field, finding.value)
            return [f"/storage/{storage.id}/{finding.field}"]
    if isinstance(finding, SubmissionOptionFinding):
        syntax = finding.syntax
        if profile.slurm is not None:
            syntax = canonical_slurm_syntax(finding.name) or syntax
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
        if option is None and syntax:
            option = SubmissionOption(name=finding.name, syntax=syntax)
            options.append(option)
        if option is not None:
            if syntax:
                option.syntax = syntax
            option.required = (
                True
                if finding.requirement == "required"
                else False
                if finding.requirement in {"recommended", "optional"}
                else None
            )
            option.support = finding.support
            option.condition = finding.condition
            prefix = (
                "slurm/options"
                if profile.slurm is not None
                else "htcondor/submit_attributes"
            )
            paths = []
            if finding.syntax:
                paths.append(f"/{prefix}/{option.name}/syntax")
            if finding.support != "unknown":
                paths.append(f"/{prefix}/{option.name}/support")
            if finding.requirement != "conditional":
                paths.append(f"/{prefix}/{option.name}/required")
            if option.condition is not None:
                paths.append(f"/{prefix}/{option.name}/condition")
            return paths
    if isinstance(finding, UnmappedSubmissionOptionFinding):
        if profile.slurm is not None:
            unmapped_options = profile.slurm.unmapped_options
            path = "/slurm/unmapped_options"
        elif profile.htcondor is not None:
            unmapped_options = profile.htcondor.unmapped_submit_attributes
            path = "/htcondor/unmapped_submit_attributes"
        else:
            return []
        if not any(
            item.documented_name == finding.documented_name
            for item in unmapped_options
        ):
            unmapped_options.append(
                UnmappedSubmissionOption(
                    documented_name=finding.documented_name,
                    documented_syntax=finding.documented_syntax,
                    requirement=finding.requirement,
                    support=finding.support,
                    condition=finding.condition,
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
                value=_finding_value(finding, field_path),
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


def _finding_value(
    finding: DocumentationFinding,
    field_path: str,
) -> bool | int | str | list[str] | None:
    if isinstance(finding, AllocationRequiredFinding):
        return finding.allocation_required
    if isinstance(finding, SubmissionOptionFinding):
        if field_path.endswith("/syntax"):
            return finding.syntax
        if field_path.endswith("/support"):
            return finding.support
        if field_path.endswith("/condition"):
            return finding.condition
        return (
            True
            if finding.requirement == "required"
            else False
            if finding.requirement in {"recommended", "optional"}
            else None
        )
    if isinstance(finding, UnmappedSubmissionOptionFinding):
        return finding.documented_name
    if isinstance(finding, PartitionFinding):
        return finding.value
    if isinstance(finding, NetworkFinding):
        return finding.available
    if isinstance(finding, ChargingModelFinding):
        return finding.charging_model
    if isinstance(finding, AccountingPolicyFinding):
        return finding.value
    if isinstance(finding, HTCondorPolicyFinding):
        return finding.value
    return finding.value


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


def _retain_walltime_conflict(
    profile: SiteProfile,
    report: EvidenceReport,
    path: str,
    documented_value: int,
    documentation_evidence_ids: list[str],
) -> None:
    """Record a measured/documented walltime disagreement and retain policy."""

    link = next((item for item in report.links if item.profile_field == path), None)
    linked_ids = set(link.evidence_ids if link else [])
    measured = [
        item
        for item in report.evidence
        if item.evidence_id in linked_ids
        and item.source_type == "measurement"
        and item.disposition == "accepted"
        and isinstance(item.value, int)
        and not isinstance(item.value, bool)
    ]
    if not measured or all(item.value == documented_value for item in measured):
        return
    if any(item.field == path for item in profile.conflicts):
        return

    measurement_ids = [item.evidence_id for item in measured]
    raw_measurement_value = measured[0].value
    if not isinstance(raw_measurement_value, int) or isinstance(
        raw_measurement_value, bool
    ):
        return
    measurement_value = raw_measurement_value
    selected_evidence = documentation_evidence_ids[0]
    selection_rule = "documentation_over_measurement_for_walltime_policy"
    note = (
        f"Measurement reported {measurement_value}; documentation reported "
        f"{documented_value}. Documentation is authoritative for the policy limit."
    )
    report.conflicts.append(
        ConflictRecord(
            field_path=path,
            selected_evidence_id=selected_evidence,
            other_evidence_ids=measurement_ids,
            selection_rule=selection_rule,
            note=note,
        )
    )
    profile.conflicts.append(
        ProfileConflict(
            field=path,
            selected_value=documented_value,
            selected_evidence=selected_evidence,
            other_evidence=measurement_ids,
            evidence_values=[
                ConflictEvidenceValue(
                    source="measurement",
                    value=measurement_value,
                    evidence_ids=measurement_ids,
                ),
                ConflictEvidenceValue(
                    source="documentation",
                    value=documented_value,
                    evidence_ids=documentation_evidence_ids,
                ),
            ],
            selection_rule=selection_rule,
            note=note,
        )
    )


def _retain_measured_partition_conflict(
    profile: SiteProfile,
    report: EvidenceReport,
    path: str,
    documented_value: bool | int | list[str],
    documentation_evidence_ids: list[str],
) -> None:
    """Keep an affirmative measured resource value and record documentation disagreement."""

    link = next((item for item in report.links if item.profile_field == path), None)
    linked_ids = set(link.evidence_ids if link else [])
    measured = [
        item
        for item in report.evidence
        if item.evidence_id in linked_ids
        and item.source_type == "measurement"
        and item.disposition == "accepted"
        and item.value not in (None, [])
    ]
    if not measured or all(item.value == documented_value for item in measured):
        return
    if any(item.field == path for item in profile.conflicts):
        return

    selected = measured[0]
    selected_value = cast(ProfileValue, selected.value)
    if not isinstance(selected_value, (bool, int, list)):
        return
    measurement_ids = [item.evidence_id for item in measured]
    selection_rule = "measurement_over_documentation_for_observable_resource"
    note = (
        f"Measurement reported {selected_value}; documentation reported "
        f"{documented_value}. The measured resource value is retained."
    )
    report.conflicts.append(
        ConflictRecord(
            field_path=path,
            selected_evidence_id=selected.evidence_id,
            other_evidence_ids=documentation_evidence_ids,
            selection_rule=selection_rule,
            note=note,
        )
    )
    profile.conflicts.append(
        ProfileConflict(
            field=path,
            selected_value=selected_value,
            selected_evidence=selected.evidence_id,
            other_evidence=documentation_evidence_ids,
            evidence_values=[
                ConflictEvidenceValue(
                    source="measurement",
                    value=selected_value,
                    evidence_ids=measurement_ids,
                ),
                ConflictEvidenceValue(
                    source="documentation",
                    value=cast(ProfileValue, documented_value),
                    evidence_ids=documentation_evidence_ids,
                ),
            ],
            selection_rule=selection_rule,
            note=note,
        )
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
