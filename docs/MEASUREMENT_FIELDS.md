# Login-node measurement fields

The machine-readable catalogs under `schemas/measurement-fields/` define facts that reviewed
collectors may safely observe from a login node. They are field catalogs, not measurement results.
The `0.2` result envelope is described in [LOGIN_MEASUREMENTS.md](LOGIN_MEASUREMENTS.md).

## Boundary

A login measurement describes what the current user can observe at collection time. It does not
establish compute-node behavior or enforced site policy.

- A visible scheduler limit is configuration evidence, not necessarily an enforced policy limit.
- A path writable on the login node is not necessarily visible or writable on compute nodes.
- Login-node Internet access does not imply compute-node Internet access.
- Installed login software does not imply compute-node availability.

Each collected value retains its status, timestamp, method, command ID, and source reference.
Unavailable values remain `null`; they are never converted to `false`, zero, or empty strings.

## Common catalog

`common.json` contains approximately 50 scheduler-independent fields in these groups:

- collection metadata;
- identity, platform, and user;
- scheduler detection;
- storage and temporary storage;
- software;
- login networking; and
- process-visible system limits.

The storage catalog supports reviewed paths, roles, existence, access, filesystem type, capacity,
and bounded link tests. The networking catalog contains only:

- DNS resolution to one fixed allowlisted hostname;
- HTTPS to one fixed allowlisted target;
- bounded local TCP bind; and
- bounded local TCP loopback.

It does not authorize arbitrary destinations, port scanning, or compute-node tests.

## Scheduler catalogs

`slurm.json` contains reviewed commands and derived fields for visible partitions, node states,
node shapes, accounts, QoS, reservations, and visible configuration. A scheduler-reported
walltime remains explicitly labeled as an observation.

`htcondor.json` contains reviewed ClassAd observations and deterministic resource-group summaries.
HTCondor resource groups are not called partitions.

## Acquisition methods

Catalog fields use only:

```text
python_api
environment_variable
fixed_command
executable_lookup
collector_function
derived
```

Fixed commands are argument arrays. Model-generated commands, arbitrary shell strings, batch jobs,
and unrestricted network probes are outside the login collector.

## What requires another evidence source

Documentation or approved pilots must establish:

- enforced limits, charging, retention, and required submission options;
- compute-node storage visibility, readability, and writability;
- compute-node DNS, Internet, bind, and loopback behavior;
- login-compute and compute-compute TCP connectivity; and
- verified TCP port ranges.
