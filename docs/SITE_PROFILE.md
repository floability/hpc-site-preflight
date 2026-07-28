# HPC Site Profile

## Purpose

An HPC site profile is a machine-readable, actionable description of what a portable workflow
needs to know to use one HPC site. The workflow package describes what the workflow needs; the
site profile describes how the target site can satisfy those needs.

The profile is not a scheduler configuration dump. It contains normalized selected values,
explicit unresolved fields, and compact links to a separate evidence report.

The implemented contract is defined by:

- `src/hpc_site_preflight/profiles/models.py`;
- `schemas/site-profile.schema.json`; and
- `src/hpc_site_preflight/evidence/bundle.py` for the detailed evidence report.

## Design

The JSON is intentionally compact. Top-level metadata is followed by one scheduler-specific
section, then storage, validation, unresolved work, conflicts, and evidence links. Network facts
are grouped under login, compute, login-compute, and compute-compute sections.

Serialized profiles preserve this schema order instead of sorting keys alphabetically. The
evidence-report reference and field-evidence links are kept at the end so the actionable policy is
read first.

Values use normalized units:

- durations in seconds;
- memory in MiB;
- disk in KiB or bytes where named explicitly; and
- resource quantities as integers.

Unknown values remain `null`. They are not converted to `false`, zero, or an empty list. Each
required unknown also produces an unresolved work item with one bounded next action.

## Scheduler organization

Slurm profiles populate `slurm.options` and `slurm.partitions`. Each partition carries its visible
node count and per-node CPU, memory, temporary disk, and GPU shape. HTCondor profiles populate
`htcondor.submit_attributes` and `htcondor.resource_groups`; those groups are deterministic
summaries of selected ClassAd attributes and are never called partitions. The unused scheduler
section is `null`.

An explicitly documented requirement without a reviewed canonical mapping is retained under
`slurm.unmapped_options` or `htcondor.unmapped_submit_attributes`. It carries evidence and a
`needs_mapping` status, but preflight must not render it into a job automatically.

One semantic submission option may have several valid forms. `syntax` is therefore an ordered
array. The first item is the preferred form:

```json
{
  "name": "account",
  "syntax": ["-A {account}", "--account={account}"],
  "required": true,
  "value": null,
  "example": "<account>",
  "allowed_values": null
}
```

## Evidence and reconciliation

`site-profile.json` contains compact evidence IDs. `evidence-report.json` contains the detailed
records, including source type, target-site scope, trust, disposition, timestamp, freshness,
documentation citation fields, safe command ID, pilot ID, conflicts, and unresolved actions.

Field-specific deterministic rules define allowed sources and precedence. For an enforced Slurm
limit, official target-site documentation precedes visible scheduler configuration and the
conflict is retained. Compute-node network and storage behavior requests an approved pilot when no
accepted source establishes it.

## Compact shape example

This shortened partial profile shows the main structures. Generated profiles include all four
common storage roles. Its values are illustrative, not current site policy.

```json
{
  "schema_version": "0.3",
  "site_id": "purdue-anvil",
  "site_name": "Purdue Anvil",
  "aliases": ["Anvil"],
  "profile_state": "partial",
  "generated_at": "2026-07-19T12:00:00Z",
  "scheduler_type": "slurm",
  "scheduler_version": "24.05.2",
  "slurm": {
    "submit_command": "sbatch",
    "options": [
      {
        "name": "partition",
        "syntax": ["-p {partition}", "--partition={partition}"],
        "required": true,
        "value": null,
        "example": "shared",
        "allowed_values": ["shared", "wholenode", "gpu"]
      }
    ],
    "unmapped_options": [],
    "partitions": [
      {
        "name": "shared",
        "available": true,
        "visible_walltime_seconds": null,
        "maximum_walltime_seconds": null,
        "node_count": null,
        "cpus_per_node": 128,
        "memory_mib_per_node": 256000,
        "temporary_disk_mib_per_node": null,
        "gpu_count_per_node": null,
        "gpu_models": [],
        "features": []
      }
    ]
  },
  "htcondor": null,
  "storage": [
    {
      "name": "scratch",
      "path_pattern": "/anvil/scratch/{username}",
      "filesystem_type": "gpfs",
      "login_readable": true,
      "login_writable": true,
      "compute_visible": null,
      "compute_readable": null,
      "compute_writable": null,
      "available_bytes": null,
      "purge_after_days": null
    }
  ],
  "network": {
    "login": {
      "hostname_patterns": ["*.anvil.rcac.purdue.edu"],
      "dns_resolution": true,
      "outbound_https": true,
      "local_tcp_bind": true,
      "local_tcp_loopback": true
    },
    "compute": {
      "hostname_patterns": [],
      "dns_resolution": null,
      "outbound_https": null,
      "local_tcp_bind": null,
      "local_tcp_loopback": null
    },
    "login_compute": {
      "tcp_connect": null,
      "verified_ports": null,
      "suggested_port_range": null
    },
    "compute_compute": {
      "tcp_connect": null,
      "verified_ports": null,
      "suggested_port_range": null
    }
  },
  "accounting": {
    "allocation_required": null,
    "visible_accounts": ["mock-account"],
    "charging_model": null
  },
  "software": {
    "module_system": null,
    "workflow_tools": [],
    "container_runtimes": []
  },
  "validation": [
    {"section": "scheduler", "state": "measured"},
    {"section": "submission", "state": "measured"},
    {"section": "resources", "state": "measured"},
    {"section": "network", "state": "requires_pilot"},
    {"section": "storage", "state": "partial"},
    {"section": "accounting", "state": "partial"},
    {"section": "software", "state": "partial"}
  ],
  "unresolved": [
    {
      "field": "/slurm/partitions/shared/maximum_walltime_seconds",
      "reason": "Visible scheduler configuration does not establish enforced policy.",
      "next_action": "additional_documentation",
      "action_id": "partition_policy_search"
    }
  ],
  "conflicts": [],
  "evidence_report": "evidence-report.json",
  "field_evidence": [
    {
      "field": "/scheduler_type",
      "evidence_ids": ["measurement:example"]
    }
  ]
}
```

## Current builder boundary

The builder consumes validated login measurements and accepted documentation
findings. Measurements populate storage path patterns, login access, login networking, scheduler,
and scheduler-specific resource fields. Slurm options and partitions live under `slurm`;
HTCondor submit attributes and resource groups live under `htcondor`. Documentation sets Boolean
submission requirements and may add limits,
retention, accounting, and compute-network policy through reviewed mappings. Missing policy and
compute-node behavior remain null and become work items. Pilot evidence is planned for Phase E.

The profile does not submit or launch a workflow.
