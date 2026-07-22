# Common Login-Node Measurement Fields

## Purpose

This document defines scheduler-independent facts that HPC Site Preflight may safely observe
from a login node. It is the human-readable companion to the machine-readable catalog at
`schemas/measurement-fields/common.json`.

The catalog is an input to later contract and collector milestones. It does not authorize shell
execution, define the final measurement-bundle envelope, or implement a collector.

## Measurement boundary

A login-node measurement is an observation about the environment visible to the collector at a
specific time. It is not automatically an operational site policy.

Examples:

- A scheduler command found on `PATH` is evidence that the command is available to the login
  session. It does not prove that every user may invoke every scheduler query.
- A visible scheduler limit is configuration evidence. It does not override a documented and
  enforced policy limit.
- A path writable from the login node is not thereby writable or visible from a compute node.
- A workflow tool installed on the login node is not thereby installed on a compute node.
- A hostname is a site-identity signal. It does not independently establish the canonical site
  identity supplied by `site-descriptor.json`.

Every field in this milestone is classified as `observation`. Documentation-derived policy and
pilot-derived behavior enter through separate evidence providers.

## Catalog record

Each machine-readable field record contains:

| Property | Meaning |
| --- | --- |
| `path` | Stable JSON Pointer-like path. `*` identifies one item in a repeated collection. |
| `category` | Common measurement section containing the field. |
| `meaning` | Exact semantics of the observed value. |
| `json_type` | JSON representation type. |
| `unit` | Normalized unit, or `none` for categorical and textual values. |
| `format` | Optional format constraint such as `date-time`, `hostname`, or `filesystem-path`. |
| `source_class` | Fixed, reviewed observation mechanism; never arbitrary shell text. |
| `missing` | Whether absence is valid, permitted absence statuses, and their interpretation. |
| `freshness` | Expected recollection class. |
| `classification` | `observation` for every common login-node measurement. |
| `profile_consumers` | Candidate compact profile fields that may consume the observation. |
| `privacy` | Handling class for potentially identifying values. |

The field catalog describes semantics rather than the final JSON envelope. Milestone 5 will
define how a value, status, timestamp, and provenance are wrapped in a measurement bundle.

Within `missing`, `allowed: true` means a valid bundle may carry no value when it also carries one
of the listed statuses. `allowed: false` means the value is structurally required; the listed
status explains why collection failed but does not make the incomplete bundle valid.

## Observation statuses

The later evidence envelope must distinguish missing information from false, zero, and empty
collections. The common catalog uses the following status vocabulary:

| Status | Meaning |
| --- | --- |
| `observed` | The collector obtained and normalized the value. |
| `not_found` | The reviewed target, file, path, variable, or executable was absent. |
| `command_unavailable` | A fixed command required for this observation was not available. |
| `permission_denied` | The site denied the safe observation. |
| `not_applicable` | The field does not apply to this environment. |
| `collection_failed` | The safe observation was attempted but did not produce a usable result. |
| `redacted` | A value was observed but intentionally removed for privacy or security. |

Collectors must not convert these states into fabricated values. For example, permission denied
is not equivalent to `false`, and an unavailable capacity query is not equivalent to zero bytes.

## Freshness classes

| Class | Expectation |
| --- | --- |
| `per_run` | Collect for every profile-building run; do not reuse as current evidence. |
| `session` | May be reused only within the same login session and run context. |
| `daily` | May be reused for at most 24 hours when its original timestamp is preserved. |
| `site_change` | Stable until the site or collector changes. |

Timestamps always describe when the value was observed. Loading saved evidence must not replace
its timestamp with the current time.

## Safe source classes

Source classes are implementation constraints, not suggested free-form commands.

| Source class | Permitted mechanism |
| --- | --- |
| `collector_clock` | Process-local UTC clock. |
| `collector_metadata` | Package constants and explicit simulate/live configuration. |
| `host_identity` | Fixed operating-system hostname APIs and local name-service resolution. |
| `platform_metadata` | Fixed OS APIs and bounded reads of standard release metadata. |
| `scheduler_detection` | Fixed executable lookup for reviewed Slurm and HTCondor commands. |
| `scheduler_version` | Fixed version query selected by the detected scheduler adapter. |
| `command_provenance` | Metadata produced by the fixed-command runner. |
| `environment_lookup` | Lookup of an allowlisted environment-variable name. |
| `filesystem_metadata` | Local metadata query for an explicit reviewed path. |
| `filesystem_capacity` | Local capacity query for an explicit reviewed path. |
| `filesystem_access_check` | Local access check for an explicit reviewed path. |
| `filesystem_create_test` | Bounded create-and-delete test inside an approved writable directory. |
| `tool_discovery` | Fixed executable lookup for an allowlisted tool name. |
| `tool_version` | Fixed version query for an allowlisted discovered tool. |

No common field requires a batch allocation, job submission, external network scan, or
model-generated command. Local hostname resolution is permitted only as an identity signal; it
does not authorize general network access.

## Field groups

### Collection and command provenance

Collection metadata records timestamps, collector version, and whether the evidence source is
`simulated` or `measured`. Command-result provenance uses stable
command IDs rather than arbitrary command strings and records status, duration, exit code, and
hashes or categories instead of placing full output in normal traces.

Full command output may be retained only in a deliberately captured evidence artifact when a
later contract requires it. It must not contain secrets and must not be copied into ordinary
`trace.jsonl` events.

### Site and host identity signals

The common identity group records the login hostname, fully qualified domain name when safely
available, and DNS suffix. These values can confirm or challenge `site-descriptor.json`, seed bounded
documentation discovery, and detect an evidence/site mismatch. They cannot select the target site
without validation against explicit site descriptor.

Usernames, home-directory contents, SSH configuration, credentials, tokens, environment dumps,
and process listings are outside the catalog.

### Platform

The platform group records OS identifier and version, kernel name and release, and machine
architecture. These values describe the login node only. They may help select compatible tools
or interpret command output, but they do not establish compute-node architecture.

### Scheduler detection

The common scheduler group provides a normalized scheduler type, visible version, reviewed
commands available on `PATH`, and the fixed signals used for detection. Slurm-specific and
HTCondor-specific observations are defined in Milestones 3 and 4.

The normalized scheduler type is one of `slurm`, `htcondor`, or `unknown`. Conflicting detection
signals must be preserved rather than silently selecting a scheduler.

### Filesystems

Each reviewed filesystem path may record:

- label and intended role;
- observed path and its source;
- existence, readability, and writability from the login node;
- visible filesystem type;
- total and available capacity in bytes; and
- bounded symlink and hard-link creation support.

Candidate paths come only from explicit site descriptor, allowlisted environment variables, or
reviewed simulation configuration. The collector must not crawl arbitrary parent directories.

Actual paths can contain usernames, allocation names, or project identifiers. Evidence artifacts
may retain the path when necessary for actionability, but normal traces should use a redacted or
templated representation. The later reconciler may convert `/home/alice` to `/home/{user}` only
through a deterministic normalization rule.

Symlink and hard-link observations use temporary names inside the selected directory, apply a
strict operation count, and clean up created entries. A failed cleanup is a collection error that
must be reported. Hard-link support applies only to the tested filesystem and directory.

### Temporary directory

The temporary-directory group records an allowlisted environment-variable name, its resolved
login-node path, existence, writability, capacity, and bounded link-test results. These fields do
not establish the value or behavior of `$TMPDIR` inside a batch allocation. Compute-node
temporary-directory behavior requires a pilot.

### Installed software

Workflow tools, module systems, and container runtimes are separate repeated collections. Each
entry may record its semantic name, login-node availability, executable path, and visible
version. Discovery is limited to an allowlist chosen by the application or site simulation.

The common catalog does not prescribe the allowlist. A later collector milestone will define it
and its fixed version-query adapters. It must not execute an unknown binary merely because it is
present on `PATH`.

## Explicitly excluded from common login measurements

The following require documentation, an approved pilot, scheduler-specific work, or user/admin
input and therefore are not common login-node observations:

- enforced walltime, job-size, charging, allocation, purge, and retention policy;
- compute-node storage visibility or writability;
- compute-node temporary-directory behavior;
- manager-to-worker or worker-to-worker connectivity;
- compute-node outbound network access or usable port ranges;
- arbitrary environment variables or complete environment dumps;
- arbitrary filesystem crawling;
- arbitrary command execution;
- port scanning; and
- workflow submission or resubmission.

## Milestone boundary

Milestone 2 defines common field semantics only. It does not:

- implement Pydantic measurement models;
- change the current measurement-bundle placeholder;
- populate Anvil, Stampede3, or Notre Dame simulations;
- define Slurm partitions or HTCondor ClassAds;
- execute a login-node command; or
- construct a site profile.

The next milestones add Slurm and HTCondor field catalogs using the same record structure.
