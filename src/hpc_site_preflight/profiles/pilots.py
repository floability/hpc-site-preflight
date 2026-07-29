"""Apply flat pilot evidence to a partially constructed site profile."""

from __future__ import annotations

from hpc_site_preflight.evidence.bundle import EvidenceReport
from hpc_site_preflight.evidence.models import (
    ConflictRecord,
    EvidenceLink,
    EvidenceRecord,
    UnresolvedAction,
)
from hpc_site_preflight.evidence.provenance import build_evidence_id
from hpc_site_preflight.probes.base import PilotResultBundle
from hpc_site_preflight.profiles.models import (
    ProfileConflict,
    SiteProfile,
    UnresolvedWorkItem,
)


def apply_pilot_results(
    profile: SiteProfile,
    report: EvidenceReport,
    pilots: PilotResultBundle,
) -> tuple[SiteProfile, EvidenceReport]:
    """Fill only fields supported by present, completed pilot observations."""

    if pilots.site_id != profile.site_id or pilots.scheduler != profile.scheduler_type:
        return profile, report

    resolved: set[str] = set()
    for index, error in enumerate(pilots.errors):
        report.evidence.append(
            EvidenceRecord(
                evidence_id=build_evidence_id(
                    "pilot",
                    profile.site_id,
                    "/pilot",
                    f"{pilots.pilot_id}:{index}",
                ),
                field_path="/pilot",
                source_type="pilot",
                scope="target_site",
                trust=(
                    "captured"
                    if pilots.evidence_source == "measured"
                    else "illustrative"
                ),
                disposition="invalid",
                observed_at=pilots.collected_at,
                freshness="per_run",
                source_reference=pilots.source_reference,
                pilot_id=pilots.pilot_id,
                reason=error,
            )
        )
    for result in pilots.storage_results or []:
        storage = next(
            (
                item
                for item in profile.storage
                if result.name.casefold()
                in {item.id.casefold(), item.name.casefold(), item.role.casefold()}
            ),
            None,
        )
        if storage is None:
            continue
        values = {
            "compute_visible": result.visible,
            "compute_readable": result.readable,
            "compute_writable": result.writable,
        }
        for field, value in values.items():
            if value is None:
                continue
            path = f"/storage/{storage.id}/{field}"
            setattr(storage, field, value)
            _append_evidence(profile, report, pilots, path, value)
            resolved.add(path)

    network_inputs = {
        "login_compute": pilots.login_compute_attempts,
        "compute_compute": pilots.compute_compute_attempts,
    }
    for section_name, attempts in network_inputs.items():
        if attempts is None or not attempts:
            continue
        if section_name == "compute_compute" and pilots.distinct_nodes is not True:
            continue
        section = getattr(profile.network, section_name)
        verified = sorted({item.port for item in attempts if item.status == "passed"})
        tcp_connect = bool(verified)

        path = f"/network/{section_name}/tcp_connect"
        evidence_id = _append_evidence(
            profile,
            report,
            pilots,
            path,
            tcp_connect,
        )
        if section.tcp_connect is not None and section.tcp_connect != tcp_connect:
            _retain_conflict(
                profile,
                report,
                path,
                section.tcp_connect,
                tcp_connect,
                evidence_id,
            )
            section.tcp_connect = None
        else:
            section.tcp_connect = tcp_connect
            resolved.add(path)

        ports_path = f"/network/{section_name}/verified_ports"
        section.verified_ports = verified
        _append_evidence(profile, report, pilots, ports_path, verified)
        resolved.add(ports_path)

        port_range = suggested_port_range(verified)
        if port_range is not None:
            range_path = f"/network/{section_name}/suggested_port_range"
            section.suggested_port_range = port_range
            _append_evidence(profile, report, pilots, range_path, port_range)
            resolved.add(range_path)

    profile.unresolved = [item for item in profile.unresolved if item.field not in resolved]
    report.unresolved = [
        item for item in report.unresolved if item.field_path not in resolved
    ]
    _update_validation(profile)
    return profile, report


def suggested_port_range(ports: list[int]) -> str | None:
    """Cover successful ports from the first occupied 1,000 block to the last."""

    if not ports:
        return None
    first = min(ports) // 1000 * 1000
    last = max(ports) // 1000 * 1000 + 999
    return f"{first}-{last}"


def _append_evidence(
    profile: SiteProfile,
    report: EvidenceReport,
    pilots: PilotResultBundle,
    path: str,
    value: bool | str | list[int],
) -> str:
    """Append and link one accepted pilot observation."""

    evidence_id = build_evidence_id(
        "pilot",
        profile.site_id,
        path,
        pilots.pilot_id,
    )
    report.evidence.append(
        EvidenceRecord(
            evidence_id=evidence_id,
            field_path=path,
            source_type="pilot",
            scope="target_site",
            trust="captured" if pilots.evidence_source == "measured" else "illustrative",
            disposition="accepted",
            value=value,
            observed_at=pilots.collected_at,
            freshness="per_run",
            source_reference=pilots.source_reference,
            pilot_id=pilots.pilot_id,
            result=value,
        )
    )
    _link(report, path, evidence_id)
    return evidence_id


def _link(
    report: EvidenceReport,
    path: str,
    evidence_id: str,
) -> None:
    """Add one evidence ID without duplicating field-link objects."""

    report_link = next((item for item in report.links if item.profile_field == path), None)
    if report_link is None:
        report.links.append(EvidenceLink(profile_field=path, evidence_ids=[evidence_id]))
    elif evidence_id not in report_link.evidence_ids:
        report_link.evidence_ids.append(evidence_id)


def _retain_conflict(
    profile: SiteProfile,
    report: EvidenceReport,
    path: str,
    documented_value: bool,
    pilot_value: bool,
    pilot_evidence: str,
) -> None:
    """Record disagreement and abstain instead of silently selecting a value."""

    linked = next((item for item in report.links if item.profile_field == path), None)
    other_evidence = [
        item for item in (linked.evidence_ids if linked else []) if item != pilot_evidence
    ]
    report.conflicts.append(
        ConflictRecord(
            field_path=path,
            selected_evidence_id=pilot_evidence,
            other_evidence_ids=other_evidence,
            selection_rule="abstain_on_network_conflict",
            note=(
                f"Documentation reported {documented_value}; the pilot reported "
                f"{pilot_value}. The profile value remains unknown."
            ),
        )
    )
    profile.conflicts.append(
        ProfileConflict(
            field=path,
            selected_value=None,
            selected_evidence=pilot_evidence,
            other_evidence=other_evidence,
            selection_rule="abstain_on_network_conflict",
            note="Documentation and pilot evidence disagree; no value was selected.",
        )
    )
    profile.unresolved = [item for item in profile.unresolved if item.field != path]
    profile.unresolved.append(
        UnresolvedWorkItem(
            field=path,
            reason="Documentation and pilot evidence disagree.",
            next_action="admin_confirmation",
            action_id="network_conflict_confirmation",
        )
    )
    report.unresolved = [item for item in report.unresolved if item.field_path != path]
    report.unresolved.append(
        UnresolvedAction(
            field_path=path,
            action="admin_confirmation",
            action_id="network_conflict_confirmation",
            reason="Documentation and pilot evidence disagree.",
        )
    )


def _update_validation(profile: SiteProfile) -> None:
    """Mark only completely evidenced sections as pilot validated."""

    connections = (profile.network.login_compute, profile.network.compute_compute)
    if all(item.tcp_connect is not None for item in connections):
        profile.section_status.network = "pilot_validated"

    observed_storage = [
        item
        for item in profile.storage
        if item.role in {"home", "scratch", "project"}
        and item.path_pattern is not None
    ]
    if observed_storage and all(
        item.compute_visible is not None
        and item.compute_readable is not None
        and item.compute_writable is not None
        for item in observed_storage
    ):
        profile.section_status.storage = "pilot_validated"
