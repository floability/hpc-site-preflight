# HPC Site Profile

## Purpose

An HPC site profile is a machine-readable, actionable description of how a workflow can use one
HPC site. The workflow specification describes requirements; the profile supplies scheduler
syntax, resource shapes and limits, storage behavior, networking, and accounting policy.

The profile is deliberately partial. Unknown values remain `null`, unsupported claims are
discarded, and conflicts remain visible.

The implemented contracts are:

- `src/hpc_site_preflight/profiles/models.py`;
- `schemas/site-profile.schema.json`; and
- `src/hpc_site_preflight/evidence/bundle.py`.

## Organization

`scheduler_type` selects exactly one scheduler section. Slurm uses options and partitions.
HTCondor uses submit attributes, a visible pool snapshot, CPU groups, and GPU groups. HTCondor
groups summarize advertised resources; they are not administrative partitions.

Slurm uses one `maximum_walltime_seconds` field. A measured value of `-1` means Slurm reported
the partition as unlimited; documented policy may override that value while retaining a conflict.

Submission syntax remains an ordered array because one semantic value can have several forms:

```json
{
  "name": "account",
  "syntax": ["-A {account}", "--account={account}"],
  "required": true,
  "example": "<account>",
  "allowed_values": null
}
```

Unknown Slurm directives are retained only when a cited span contains literal Slurm option
syntax. Unknown HTCondor attributes require literal submit-description assignment syntax.
Procedural prose and unrelated commands do not become scheduler options. Unmapped entries are
never rendered automatically.

## Evidence

The profile ends with `evidence_id`. The detailed evidence artifact is named
`evidence-<evidence_id>.json` and carries the same ID as `report_id`. Field-to-evidence links,
exact quotations, URLs, measurements, pilot results, conflicts, and unresolved actions remain in
that debugging artifact instead of enlarging the operational profile.

## Compact Slurm example

```json
{
  "schema_version": "0.4",
  "site_id": "purdue-anvil",
  "site_name": "Purdue Anvil",
  "aliases": ["Anvil"],
  "profile_state": "partial",
  "generated_at": "2026-07-29T12:00:00Z",
  "scheduler_type": "slurm",
  "slurm": {
    "version": "25.11.1",
    "submit_command": "sbatch",
    "default_partition": "shared",
    "options": [],
    "unmapped_options": [],
    "partitions": [
      {
        "name": "shared",
        "maximum_walltime_seconds": 345600,
        "maximum_nodes_per_job": 1,
        "shared_nodes": true,
        "node_count": 250,
        "cpus_per_node": 128,
        "memory_mib_per_node": 257400,
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
      "id": "scratch",
      "name": "Scratch",
      "role": "scratch",
      "environment_variables": ["SCRATCH"],
      "path_pattern": "/anvil/scratch/{username}",
      "filesystem_type": "gpfs",
      "login_readable": true,
      "login_writable": true,
      "compute_visible": true,
      "compute_readable": true,
      "compute_writable": true,
      "shared_across_compute_nodes": null,
      "backup_policy": null,
      "purge_after_days": 30,
      "purge_condition": "inactive files"
    }
  ],
  "network": {
    "login": {
      "hostname_patterns": ["*.anvil.rcac.purdue.edu"],
      "dns_resolution": null,
      "outbound_https": null,
      "local_tcp_bind": null,
      "local_tcp_loopback": null
    },
    "compute": {
      "hostname_patterns": [],
      "dns_resolution": null,
      "outbound_https": null,
      "local_tcp_bind": null,
      "local_tcp_loopback": null
    },
    "login_compute": {
      "tcp_connect": true,
      "verified_ports": [30000, 30801],
      "suggested_port_range": "30000-30999"
    },
    "compute_compute": {
      "tcp_connect": true,
      "verified_ports": [30000, 30965],
      "suggested_port_range": "30000-30999"
    }
  },
  "accounting": {
    "allocation_required": true,
    "charging_unit": "service unit",
    "charging_model": "allocated core-hours",
    "filesystem_storage_charged": null
  },
  "section_status": {
    "scheduler": "measured",
    "submission": "documented",
    "resources": "documented",
    "network": "pilot_validated",
    "storage": "partial",
    "accounting": "documented"
  },
  "unresolved": [],
  "conflicts": [
    {
      "field": "/slurm/partitions/shared/maximum_walltime_seconds",
      "selected_value": 345600,
      "selected_evidence": "documentation:shared-walltime",
      "other_evidence": ["measurement:shared-walltime"],
      "evidence_values": [
        {
          "source": "measurement",
          "value": -1,
          "evidence_ids": ["measurement:shared-walltime"]
        },
        {
          "source": "documentation",
          "value": 345600,
          "evidence_ids": ["documentation:shared-walltime"]
        }
      ],
      "selection_rule": "documentation_over_measurement_for_walltime_policy",
      "note": "Documentation overrides Slurm's reported unlimited configuration."
    }
  ],
  "evidence_id": "report-example"
}
```

## HTCondor difference

The common storage, network, accounting, status, unresolved, conflict, and evidence sections stay
the same. Only the scheduler section changes:

```json
{
  "scheduler_type": "htcondor",
  "slurm": null,
  "htcondor": {
    "version": "23.7.2",
    "submit_command": "condor_submit",
    "collector_host": "condorfe.example.edu",
    "file_transfer_supported": true,
    "submit_attributes": [
      {
        "name": "request_cpus",
        "syntax": ["request_cpus = {count}"],
        "required": null,
        "example": "1",
        "allowed_values": null
      }
    ],
    "unmapped_submit_attributes": [],
    "pool_totals": {
      "machine_count": 362,
      "cpu_cores": 12938,
      "memory_mib": 88935856,
      "advertised_gpus": 309
    },
    "cpu_groups": [
      {
        "cpu_cores_per_machine": 24,
        "machine_count": 227,
        "memory_mib_min": 31591,
        "memory_mib_max": 256715
      }
    ],
    "gpu_groups": [
      {
        "gpu_count_per_machine": 4,
        "cpu_cores_per_machine": 24,
        "machine_count": 27,
        "memory_mib_min": 128268,
        "memory_mib_max": 192580
      }
    ]
  }
}
```

Pool counts are timestamped observations, not promises of immediately available capacity.
