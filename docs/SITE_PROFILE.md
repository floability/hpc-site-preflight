# HPC Site Profile

## Purpose

An HPC site profile is a machine-readable and actionable description of what a portable
workflow needs to know to submit and execute a batch job at one HPC site.

A portable workflow package describes workflow intent: software, data, resources, and
connectivity requirements. A site profile describes the other side of that contract:

- which scheduler and submission command the site uses;
- which submission options are required and how to render them;
- which partitions or HTCondor resource groups are suitable;
- which resource and walltime limits apply;
- which storage paths are visible and writable from compute nodes;
- which network behaviors are available;
- which accounting, charging, retention, and purge rules matter; and
- which facts remain unknown and what evidence action could resolve them.

The profile is not a scheduler configuration dump. Visible configuration is evidence, but it
may differ from documented or enforced policy. The profile contains the operational value
selected by deterministic reconciliation and records the evidence used to select it.

This document is the working design contract. The Pydantic models will be aligned with it in a
later focused milestone after the profile contents have been reviewed.

## Design principles

### Actionable

Values use normalized, machine-consumable forms. Durations are stored in seconds, memory in
MiB, counts as integers, and scheduler options as rendering templates. Human-readable source
text remains in the evidence report.

### Partial first

Profile construction should retain every verified field it can produce. Failure to discover,
extract, measure, or pilot one field does not discard other valid fields. An unresolved value
is represented as `null`, and the `unresolved` section states why it is missing and which safe
action could fill it.

`profile_state` describes completeness:

- `complete`: every field required by the selected profile contract is resolved or explicitly
  not applicable;
- `partial`: at least one requested field remains unresolved.

Completeness is independent of evidence validation. A partial profile may still contain many
individually verified and immediately useful values.

### Evidence backed

The compact profile contains evidence identifiers, not full pages or long quotations. A
separate evidence report stores exact quotes, URLs, headings, retrieval details, measurement
observations, pilot results, timestamps, scope decisions, and rejected evidence.

Profile field paths use JSON Pointer notation when referring to evidence or unresolved work.
For example:

```text
/partitions/limits/wholenode/maximum_walltime_seconds
```

### Deterministically reconciled

When sources disagree, the profile keeps an actionable selected value and records the conflict.
Selection is performed by a field-specific deterministic rule. For an enforced operational
limit, official target-site documentation may take precedence over visible scheduler
configuration, while both observations remain in the evidence report.

A confidence scale is intentionally not defined yet. It should be added only after its meaning,
calibration, and relationship to validation status are specified.

### Scheduler aware

Slurm and HTCondor share the same top-level profile contract but use different resource
organization concepts:

- Slurm sites populate `partitions`.
- HTCondor sites populate `resource_groups` derived from observable ClassAd attributes.

HTCondor resource groups must not be labeled as administrative partitions.

## Profile and evidence artifacts

A profile-building run produces at least two related artifacts:

1. `site-profile.json` contains compact values consumed by the preflight planner.
2. `evidence-report.json` contains detailed provenance, conflicts, validation results, and
   acquisition diagnostics used for research and auditing.

The profile points to the evidence report and maps actionable fields to stable evidence IDs.
The profile must remain usable without loading the entire evidence report, but a consumer must
be able to audit every selected policy value.

## Submission options and multiple syntax forms

One semantic scheduler option may have several valid spellings. The profile therefore stores
`syntax` as an ordered list rather than a single string:

```json
{
  "name": "account",
  "syntax": [
    "-A {account}",
    "--account={account}"
  ],
  "requirement": "required",
  "example": "<account>",
  "value": null,
  "allowed_values": null
}
```

The option `name` is the stable semantic identifier. Syntax entries are renderable templates;
their placeholders are filled from the backpack, profile defaults, or user-provided values.
The first syntax is the preferred representation unless a later planner rule selects another
documented form.

`requirement` is one of:

- `required`: the generated submission must contain the option;
- `recommended`: normally emitted, but not mandatory for validity;
- `optional`: emitted only when requested by the workflow;
- `conditional`: required only when its documented condition is satisfied.

`value` is a site-selected or default value when one exists. It remains `null` when the value
must come from the workflow, allocation, user, or planner. `allowed_values` is `null` when the
site does not publish a bounded list; it must not be guessed.

## Validation and unresolved work

The compact `validation` section summarizes each major section using states such as:

- `measured`;
- `documented`;
- `pilot_validated`;
- `partial`;
- `requires_pilot`;
- `conflicting`;
- `not_applicable`.

The detailed report records validation per field. The `unresolved` array is the machine-readable
work order for later evidence actions. A missing field can request a predefined pilot, more
documentation, user input, or administrator confirmation. It must never request an arbitrary
model-generated probe.

## Full illustrative profile

The following profile uses Purdue Anvil-shaped values from an earlier prototype to demonstrate
the proposed contract. It is an illustrative development fixture, not a current or authoritative
statement of Purdue policy.

```json
{
  "schema_version": "0.1",
  "site": {
    "id": "purdue-anvil",
    "name": "Purdue Anvil",
    "aliases": [
      "Anvil"
    ]
  },
  "profile_state": "partial",
  "generated_at": "2026-07-18T18:00:00Z",
  "scheduler": {
    "type": "slurm",
    "submit_command": "sbatch",
    "version": null
  },
  "submission": {
    "options": [
      {
        "name": "account",
        "syntax": [
          "-A {account}",
          "--account={account}"
        ],
        "requirement": "required",
        "example": "<account>",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "partition",
        "syntax": [
          "-p {partition}",
          "--partition={partition}"
        ],
        "requirement": "required",
        "example": "shared",
        "value": null,
        "allowed_values": [
          "debug",
          "gpu-debug",
          "wholenode",
          "wide",
          "shared",
          "highmem",
          "gpu",
          "ai"
        ]
      },
      {
        "name": "nodes",
        "syntax": [
          "--nodes={count}",
          "-N {count}"
        ],
        "requirement": "required",
        "example": "1",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "ntasks",
        "syntax": [
          "--ntasks={count}",
          "-n {count}"
        ],
        "requirement": "required",
        "example": "1",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "cpus-per-task",
        "syntax": [
          "--cpus-per-task={count}"
        ],
        "requirement": "required",
        "example": "2",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "mem",
        "syntax": [
          "--mem={size}",
          "--mem={size}G"
        ],
        "requirement": "required",
        "example": "1G",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "job-name",
        "syntax": [
          "--job-name={name}",
          "-J {name}"
        ],
        "requirement": "required",
        "example": "example-job",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "time",
        "syntax": [
          "-t {time}",
          "--time={time}"
        ],
        "requirement": "required",
        "example": "01:30:00",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "gpus",
        "syntax": [
          "--gpus={count}",
          "--gres=gpu:{count}"
        ],
        "requirement": "conditional",
        "example": "1",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "output",
        "syntax": [
          "--output={path}",
          "-o {path}"
        ],
        "requirement": "recommended",
        "example": "logs/%x-%j.out",
        "value": null,
        "allowed_values": null
      },
      {
        "name": "error",
        "syntax": [
          "--error={path}",
          "-e {path}"
        ],
        "requirement": "optional",
        "example": "logs/%x-%j.err",
        "value": null,
        "allowed_values": null
      }
    ]
  },
  "partitions": {
    "default": "shared",
    "limits": {
      "debug": {
        "maximum_walltime_seconds": 7200,
        "maximum_nodes": 2,
        "shared_nodes": false,
        "gpu_required": false
      },
      "gpu-debug": {
        "maximum_walltime_seconds": 1800,
        "maximum_nodes": 1,
        "shared_nodes": false,
        "gpu_required": true
      },
      "wholenode": {
        "maximum_walltime_seconds": 345600,
        "maximum_nodes": 16,
        "shared_nodes": false,
        "gpu_required": false
      },
      "wide": {
        "maximum_walltime_seconds": 43200,
        "maximum_nodes": 56,
        "shared_nodes": false,
        "gpu_required": false
      },
      "shared": {
        "maximum_walltime_seconds": 345600,
        "maximum_nodes": 1,
        "shared_nodes": true,
        "gpu_required": false
      },
      "highmem": {
        "maximum_walltime_seconds": 172800,
        "maximum_nodes": 1,
        "shared_nodes": false,
        "gpu_required": false
      },
      "gpu": {
        "maximum_walltime_seconds": 172800,
        "maximum_nodes": null,
        "shared_nodes": false,
        "gpu_required": true
      },
      "ai": {
        "maximum_walltime_seconds": 172800,
        "maximum_nodes": null,
        "shared_nodes": false,
        "gpu_required": true
      }
    }
  },
  "resource_groups": {},
  "network": {
    "manager_worker": {
      "reachable": null,
      "direction": "worker_to_manager"
    },
    "worker_worker": {
      "reachable": null
    },
    "port_ranges": null,
    "manager_address": null,
    "outbound_compute": null
  },
  "storage": {
    "shared_filesystems": [
      {
        "name": "home",
        "path_template": "/home/{user}",
        "compute_visible": null,
        "compute_writable": null,
        "purge_after_days": null
      },
      {
        "name": "scratch",
        "path_template": "/anvil/scratch/{user}",
        "compute_visible": true,
        "compute_writable": null,
        "purge_after_days": null
      }
    ],
    "temporary_directory": {
      "environment_variable": "TMPDIR",
      "compute_writable": null,
      "minimum_capacity_mb": null
    },
    "symlink_supported": null,
    "hardlink_supported": null
  },
  "accounting": {
    "allocation_required": true,
    "charging_model": null,
    "charging_notes": null
  },
  "software": {
    "workflow_tools": {},
    "module_system": null,
    "container_runtimes": []
  },
  "validation": {
    "scheduler": "measured",
    "submission": "documented",
    "partitions": "conflicting",
    "network": "requires_pilot",
    "storage": "partial",
    "accounting": "partial",
    "software": "partial"
  },
  "unresolved": [
    {
      "field": "/network/manager_worker/reachable",
      "reason": "Login-node observation and documentation cannot establish compute-node reachability.",
      "next_action": "run_pilot",
      "action_id": "manager_to_worker_connectivity"
    },
    {
      "field": "/network/worker_worker/reachable",
      "reason": "Worker-to-worker connectivity requires observations from allocated compute nodes.",
      "next_action": "run_pilot",
      "action_id": "worker_to_worker_connectivity"
    },
    {
      "field": "/network/outbound_compute",
      "reason": "No verified target-site documentation value was found.",
      "next_action": "run_pilot",
      "action_id": "outbound_network_access"
    },
    {
      "field": "/storage/shared_filesystems/0/compute_visible",
      "reason": "Login-node visibility does not prove compute-node visibility.",
      "next_action": "run_pilot",
      "action_id": "shared_storage_visibility"
    },
    {
      "field": "/accounting/charging_model",
      "reason": "The bounded documentation search did not produce verified charging evidence.",
      "next_action": "additional_documentation",
      "action_id": "charging_policy_search"
    }
  ],
  "conflicts": [
    {
      "field": "/partitions/limits/wholenode/maximum_walltime_seconds",
      "selected_value": 345600,
      "selected_evidence": "documentation:anvil-partition-limits",
      "other_evidence": [
        "measurement:slurm-visible-walltime"
      ],
      "selection_rule": "documented_operational_policy_over_visible_scheduler_configuration",
      "note": "The documentation states a four-day limit while visible scheduler configuration reports no limit."
    }
  ],
  "provenance": {
    "run_id": "example-run",
    "evidence_report": "evidence-report.json",
    "evidence_bundle_id": "purdue-anvil-example-evidence",
    "field_evidence": {
      "/scheduler/type": [
        "measurement:scheduler-type"
      ],
      "/scheduler/submit_command": [
        "documentation:slurm-submission"
      ],
      "/submission/options": [
        "documentation:slurm-submission-options"
      ],
      "/partitions/default": [
        "documentation:default-partition"
      ],
      "/partitions/limits": [
        "documentation:anvil-partition-limits",
        "measurement:slurm-visible-partitions"
      ],
      "/storage/shared_filesystems/1/path_template": [
        "measurement:scratch-path"
      ],
      "/storage/shared_filesystems/1/compute_visible": [
        "documentation:scratch-compute-visibility"
      ],
      "/accounting/allocation_required": [
        "documentation:allocation-requirement"
      ]
    }
  }
}
```

## How preflight consumes the profile

The deterministic preflight planner combines backpack requirements with the compact profile:

1. Select a compatible partition or HTCondor resource group.
2. Check worker count, cores, memory, GPUs, and walltime against known limits.
3. Verify required storage and network capabilities.
4. Resolve each required submission option from the profile, backpack, or user input.
5. Render the selected scheduler syntax into an execution plan.
6. Return a structured blocked result when a required value is incompatible or unresolved.

The profile does not submit or launch the workflow. It provides the validated site-side inputs
needed to construct a plan safely.
