# Implementation Milestones

## Research goal

HPC Site Preflight supports the WORKS 2026 paper:

> The Other Half of Workflow Portability: Evidence-Backed HPC Site Policies with Agentic
> Discovery

Portable workflow packages describe what a workflow needs. This project constructs the other
side of the deployment contract: a machine-readable and actionable description of what a
particular HPC site requires, permits, and exposes to a batch job.

The system acquires three classes of site knowledge in increasing cost order:

1. facts observable safely from a login node;
2. operational policy stated in site documentation;
3. behavior observable only through a predefined pilot job.

The resulting site profile is partial-first and evidence-backed. Missing or invalid individual
fields do not discard valid fields. Every selected policy value is linked to evidence, conflicts
are retained, and reconciliation is deterministic. The profile is then compared with a portable
workflow package to produce a site-specific execution plan or a structured blocked result with
remediation.

The paper evaluates Purdue Anvil, TACC Stampede3, and Notre Dame CRC, covering Slurm and
HTCondor. Documentation context is compared in three modes:

```text
full-corpus
bm25
schema-expanded-bm25
```

## Development strategy

Development is mock-first and laptop-first.

- `mock` mode uses reviewed JSON fixtures for HPC login measurements and pilot results. It must
  not require access to an HPC system.
- Documentation tests use captured pages, corpora, and provider responses by default. Explicitly
  marked integration runs may use approved web access and model APIs.
- `live` mode is deferred until the complete mock pipeline works for all three evaluation sites.
- Mock and live implementations must eventually conform to the same provider interfaces and
  external JSON contracts.

The current `replay` name is provisional and will be replaced by `mock` in a focused milestone.
Mock evidence represents the shape and behavior of evidence that will later be captured live; it
must not return invented success values from unfinished operations.

Each milestone is intentionally small enough for one focused coding session. For every
milestone:

1. read this milestone and the relevant design document;
2. inspect existing interfaces and tests;
3. implement only the milestone;
4. add or update offline tests;
5. run `pytest`;
6. run `python -m compileall src`;
7. report behavior changes, assumptions, and unresolved items; and
8. stop without beginning the next milestone.

The live-site phase begins only after an explicit decision that the laptop pipeline and paper
experiments are ready. Promotion into a separate production repository, if desired, is a later
decision rather than an implicit part of a development milestone.

---

## Phase A — Foundation

## Milestone 1 — Package, CLI, and performance tracker — complete

Implemented and verified:

- installable Python 3.11+ `src/` package;
- top-level CLI and nested command hierarchy;
- one `RunTracker` per parsed command;
- stage and run timing, model usage, retries, and tool-call reporting;
- durable `performance.json` and `trace.jsonl` artifacts;
- explicit `FeatureNotImplementedError` failures for unfinished operations;
- CLI, tracker, packaging, lint, and strict typing tests.

No later pipeline behavior is considered implemented by this milestone.

---

## Phase B — Define measurable site evidence

These are the next milestones. The field catalogs and mock files are reviewed before measurement
loaders or collectors are implemented. They become the source material for the typed contracts.

## Milestone 2 — Common login-node measurement field catalog

Create a design document that enumerates scheduler-independent facts that can be observed safely
from a login node.

For every field, record:

- stable field path and meaning;
- JSON type and normalized unit;
- observation source or safe command class;
- whether absence is valid;
- expected freshness;
- whether the value is an observation or an operational policy;
- safety and privacy constraints; and
- which site-profile field may consume it.

Cover at least:

- site and host identity signals;
- operating system and CPU architecture;
- scheduler detection and visible version;
- available scheduler commands;
- filesystem paths, capacity, writability, symlinks, and hard links;
- temporary-directory variables and behavior observable from the login node;
- installed workflow tools, module systems, and container runtimes; and
- collection timestamps and command-result provenance.

Do not implement shell execution or Pydantic models yet.

Acceptance:

- the catalog distinguishes observations from policy;
- every field has a type, unit, and missing-value rule;
- no field requires a batch allocation or unrestricted probe.

## Milestone 3 — Slurm measurement field catalog

Define the Slurm-specific facts that a future collector may observe without claiming they are
enforced policy.

Cover at least:

- cluster identity and Slurm version;
- partitions, default partition, visibility, availability, and state;
- node counts and node states;
- CPU, memory, temporary disk, feature, and node-shape summaries;
- GRES and GPU count/model observations;
- visible accounts, associations, QoS, reservations, and limits where permitted;
- visible scheduler configuration values relevant to submission; and
- permission-denied, hidden, unavailable, and command-missing outcomes.

For each field, identify the likely Slurm command and output concept, but do not implement command
execution. Explicitly document examples where visible configuration may disagree with policy,
including the walltime-limit case motivating the paper.

Acceptance:

- every field maps to the common evidence envelope;
- partition and node-shape observations are distinct;
- visible configuration is never labeled as enforced policy.

## Milestone 4 — HTCondor measurement field catalog

Define the HTCondor-specific facts that a future collector may observe from collectors,
schedulers, and machine ClassAds.

Cover at least:

- collector and schedd identity;
- HTCondor version and visible pool identity;
- execute machines and slot counts;
- partitionable, dynamic, and static slot types;
- slot state and activity;
- CPUs, memory, disk, GPUs, architecture, and operating-system attributes;
- machine family and administrator-defined grouping attributes;
- observable job-submission capabilities; and
- permission-denied, hidden, unavailable, and command-missing outcomes.

Define how normalized resource groups are derived from selected ClassAd attributes. Do not call
them partitions, and do not assume an administrator-defined group exists.

Acceptance:

- every field maps to the common evidence envelope;
- raw ClassAd observations are distinguishable from derived resource groups;
- Slurm-only concepts do not appear in the HTCondor contract.

## Milestone 5 — Site information and mock evidence envelope

Finalize the JSON envelopes shared by the three evaluation sites before writing site-specific
fixtures.

Define:

- `site-info.json` identity and approved documentation scope;
- a measurement-bundle envelope with schema version, site ID, scheduler, timestamps, source mode,
  collector version, and common/scheduler/storage/software sections;
- per-observation status and provenance;
- explicit unavailable and permission-denied representations; and
- the directory layout under `examples/mock/<site-id>/`.

Replace the planned execution-mode vocabulary `live|replay` with `live|mock` in design documents
only. Code and file migration happen in a later milestone.

Acceptance:

- one envelope can carry both Slurm and HTCondor measurements;
- no scheduler SDK or command-output type escapes the scheduler section;
- unknown values remain distinguishable from false, zero, and empty collections.

## Milestone 6 — Purdue Anvil mock measurement fixture

Create reviewed Anvil `site-info.json` and `login-measurements.json` fixtures using the common and
Slurm catalogs.

Requirements:

- include representative partitions and node shapes;
- include the visible walltime observation used by the motivating conflict scenario;
- label every value as captured, an illustrative mock observation, or intentionally unresolved;
- do not silently turn illustrative values into authoritative current policy; and
- validate the JSON syntax and catalog coverage manually.

Do not implement loaders or reconciliation.

## Milestone 7 — TACC Stampede3 mock measurement fixture

Create reviewed Stampede3 `site-info.json` and `login-measurements.json` fixtures using the same
Slurm contract as Anvil.

The fixture must demonstrate that the contract handles two Slurm sites without adding
site-specific model fields. Site differences belong in values and evidence, not Python class
structure.

Do not implement loaders or reconciliation.

## Milestone 8 — Notre Dame CRC mock measurement fixture

Create reviewed Notre Dame CRC `site-info.json` and `login-measurements.json` fixtures using the
common and HTCondor catalogs.

The fixture must include representative machine and slot attributes and at least one derived
resource-group example. `partitions` must not be used to represent the HTCondor pool.

Do not implement loaders or reconciliation.

## Milestone 9 — Typed site and measurement contracts

Implement Pydantic models derived from the reviewed catalogs and fixtures.

Implement only:

- site-information validation;
- common measurement models;
- Slurm measurement models;
- HTCondor measurement models;
- normalized observation status and provenance; and
- schema-version validation.

Tests must validate all three site fixtures and reject scheduler-specific fields in the wrong
scheduler contract.

Do not construct a site profile.

## Milestone 10 — Mock measurement provider

Implement a provider that loads and validates mock measurement bundles from the laptop fixtures.

Requirements:

- use the same `MeasurementProvider` interface reserved for live collection;
- verify that fixture site ID and scheduler match `site-info.json`;
- record loading and validation as separate tracker stages;
- preserve observation provenance unchanged; and
- rename replay-oriented code, CLI choices, examples, and tests to `mock`.

Do not run shell commands or build a site profile.

---

## Phase C — Formalize the profile and evidence contracts

## Milestone 11 — Actionable site-profile schema

Convert `docs/SITE_PROFILE.md` into strict Pydantic models and checked JSON Schema.

Cover:

- site identity and profile completeness;
- scheduler type and submission command;
- ordered multiple syntax forms for one semantic submission option;
- Slurm partitions and HTCondor resource groups;
- normalized resource, network, storage, accounting, and software fields;
- section validation states;
- unresolved work items; and
- compact provenance references.

Use normalized units. Preserve `null` for unresolved values. Do not implement profile
construction.

## Milestone 12 — Detailed evidence-report contract

Define and implement the detailed artifact referenced by a compact site profile.

Cover:

- stable evidence IDs and field paths;
- source type, target-site scope, trust, timestamp, and freshness;
- documentation URL, heading, exact quote, and chunk provenance;
- measurement observation and safe command identifier;
- pilot identifier and result;
- accepted, rejected, and invalid evidence;
- conflict records; and
- links from compact profile fields to evidence IDs.

The normal trace must not contain secrets or full downloaded page bodies.

## Milestone 13 — Reconciliation and unresolved-action rule tables

Formalize deterministic field-specific rules before implementing the reconciler.

Define:

- which sources can establish each profile field;
- precedence when sources agree or disagree;
- when official documentation is selected over visible scheduler configuration;
- how the selected value retains a conflict note;
- when a field requests a pilot, more documentation, user input, or administrator confirmation;
  and
- when a field is not applicable.

Represent rules as typed data or small deterministic functions with table-driven tests. Do not
implement the full pipeline.

## Milestone 14 — Measurement-only partial profile builder

Construct the first useful partial site profile from validated mock login measurements only.

Requirements:

- populate fields that measurements can establish;
- leave policy-only and pilot-only fields `null`;
- emit unresolved work items instead of failing for missing fields;
- write a compact profile and detailed evidence report; and
- support Anvil, Stampede3, and Notre Dame CRC with one code path.

This milestone deliberately excludes documentation and pilot evidence.

---

## Phase D — Documentation evidence on a laptop

## Milestone 15 — Documentation provider contract and mock result

Finalize `DocumentationPolicyProvider` inputs and outputs and create a validated mock
documentation result for one site.

The result must contain a partial policy, detailed field evidence, unresolved documentation
questions, context mode, corpus identity, and provider-reported usage availability.

Do not migrate discovery yet.

## Milestone 16 — Prototype migration inventory

Inspect `hpc-site-policy-agent` and map reusable modules and tests to this repository's adapter
boundary.

Record:

- code to adapt;
- code to rewrite;
- code not to migrate;
- provider dependencies;
- artifact-schema differences; and
- tests that demonstrate bounded discovery, scope filtering, grounding, and partial output.

Update `docs/MIGRATION_FROM_POLICY_AGENT.md`. Do not copy the old top-level control loop.

## Milestone 17 — Bounded agentic documentation discovery

Adapt the bounded search/fetch/finish discovery behavior behind the documentation provider.

Requirements:

- approved-domain enforcement;
- search, page, step, timeout, and cost budgets;
- local validation of every requested action and URL;
- deterministic fallback to partial discovery state; and
- tracker stages for discovery and provider usage.

Offline tests use recorded search and page fixtures. No unrestricted browsing is allowed.

## Milestone 18 — Site scope, trust, and page normalization

Implement deterministic target-site, organization-general, sibling-site, and unrelated scope
classification. Keep scope independent of trust.

Fetch and normalize readable target-site content while preserving URL, title, headings, tables,
timestamps, hashes, and scope. Sibling pages may be retained as negative controls but cannot be
used as target-site policy.

## Milestone 19 — Persistent corpus and chunking

Implement the content-hashed persistent corpus:

- manifest, documents, and chunks;
- canonical URLs and content hashes;
- heading-aware chunks;
- atomic Markdown tables;
- intact policy and FAQ sections where appropriate;
- deterministic refresh behavior; and
- no persisted vector index.

## Milestone 20 — Full-corpus context mode

Implement deterministic `full-corpus` context construction, size accounting, truncation rules,
and field coverage reporting.

Do not implement BM25 in this milestone.

## Milestone 21 — BM25 context mode

Implement transient CPU-only BM25 retrieval with field-specific queries, scope filtering before
ranking, local deduplication, and retrieved-but-uncited tracking.

Do not implement expanded queries in this milestone.

## Milestone 22 — Schema-expanded BM25 context mode

Implement deterministic schema-derived query expansion on top of the same BM25 index. Preserve
base and expanded query provenance so the paper can compare retrieval behavior.

## Milestone 23 — Constrained documentation extraction

Implement schema-constrained extraction that proposes typed field values and selects field-local
evidence span IDs.

Python, not the model, inserts exact quotes, URLs, headings, chunk IDs, and provenance. Provider
adapters must report only provider-supplied token usage.

## Milestone 24 — Field-level documentation evidence validation

Validate every extracted field independently:

- requested field and type;
- retrieved chunk membership;
- URL and heading consistency;
- literal quote containment;
- target-site scope; and
- field-local context compatibility.

Discard only invalid fields. Preserve valid fields when another field, extraction group, retry,
or model call fails.

## Milestone 25 — Documentation evaluation command

Complete `evaluate documentation` for all three context modes.

Emit:

- documentation-derived partial profile;
- detailed evidence report;
- corpus and retrieval identifiers;
- per-stage time and provider usage;
- verified, null, invalid, and unresolved field counts; and
- sibling-site rejection metrics.

The command must run offline with captured fixtures and support explicitly marked provider
integration runs from a laptop.

---

## Phase E — Mock pilots and deterministic reconciliation

## Milestone 26 — Approved pilot catalog

Define the fixed pilot registry and its target profile fields:

- shared-storage visibility;
- compute-node write access;
- temporary-directory usability;
- outbound network access;
- manager-to-worker connectivity;
- worker-to-worker connectivity;
- bounded approved candidate-port tests; and
- scheduler-option acceptance.

Specify typed inputs, bounded behavior, expected result schema, scheduler applicability, and
safety limits. Do not submit jobs.

## Milestone 27 — Mock pilot results and provider

Create typed mock pilot-result fixtures for the three evaluation sites and implement a mock
provider that loads them through the future live-pilot interface.

Verify site ID, scheduler, pilot registry membership, timestamps, and target-field association.
Record loading and validation as tracker stages.

## Milestone 28 — Full deterministic evidence reconciliation

Combine profile candidates, mock measurements, documentation evidence, and mock pilot results.

Requirements:

- apply the reviewed field-specific rules;
- select documentation for documented operational policy when visible configuration conflicts;
- retain the alternative observation and a human-readable conflict note;
- never resolve a conflict silently;
- emit a partial profile whenever usable evidence exists; and
- preserve unresolved fields as work items.

Do not load backpacks or perform preflight checks.

---

## Phase F — Workflow requirements and preflight

## Milestone 29 — Backpack requirement contract and loader

Define and load normalized workflow requirements for scheduler backend, workers, cores, memory,
GPUs, walltime, storage, temporary space, manager-worker networking, worker-worker networking,
and outbound access.

Invalid backpack structure produces a structured validation artifact. It does not trigger site
measurement or documentation work.

## Milestone 30 — Deterministic compatibility checks

Implement field-level checks for:

- scheduler compatibility;
- required submission values;
- partition or resource-group compatibility;
- cores, memory, GPUs, worker count, and walltime;
- storage visibility and capacity;
- temporary-directory behavior; and
- required network paths.

Each issue must identify the backpack requirement, site-profile field, reason, and deterministic
remediation category.

## Milestone 31 — Execution-plan rendering

For compatible inputs, select documented scheduler option syntax and render a site-specific
execution plan. Preserve semantic option names and selected syntax in the output for auditing.

For incompatible or unresolved required inputs, emit a structured blocked result with concrete
remediation. Never submit or launch the workflow.

---

## Phase G — Complete laptop pipeline

## Milestone 32 — End-to-end mock profile build

Complete `profile build --mode mock`:

```text
site information
→ mock login measurements
→ documentation evidence
→ mock pilot results
→ deterministic reconciliation
→ partial or complete site profile
```

Every stage uses one shared `RunTracker`. The command writes a usable profile and evidence report
when at least one valid field is available, while recording recoverable stage failures and
unresolved fields.

## Milestone 33 — End-to-end mock preflight scenarios

Run backpack preflight from a laptop against mock-built profiles for:

- one compatible Slurm workflow;
- one early Slurm failure;
- one compatible HTCondor workflow;
- one HTCondor resource-group failure; and
- one blocked result caused by an unresolved field.

Store expected execution-plan or blocked-result fixtures and test them deterministically.

## Milestone 34 — Three-site regression suite

Freeze reviewed mock inputs and expected normalized outputs for Anvil, Stampede3, and Notre Dame
CRC.

Test:

- schema validity;
- partial-output behavior;
- conflict retention and documented-policy selection;
- Slurm/HTCondor normalization;
- evidence links;
- deterministic output excluding run timestamps and IDs; and
- repeatability across runs.

This milestone is the laptop-readiness gate. Do not begin live collection automatically.

---

## Phase H — Paper evaluation

## Milestone 35 — Evaluation dataset and ground-truth protocol

Define the evaluated site-profile fields, annotation procedure, acceptable evidence, abstention
rules, conflict labels, and adjudication process for the three sites.

Separate fixture construction from ground-truth labels to avoid evaluating the system against its
own generated output.

## Milestone 36 — Documentation context-mode experiment

Automate repeated `full-corpus`, `bm25`, and `schema-expanded-bm25` runs with identical discovery
inputs and extraction schema.

Measure:

- retrieval recall;
- verified field precision and recall;
- abstention accuracy;
- sibling-site rejection;
- retrieved-but-uncited evidence;
- input/output tokens when provider-reported;
- runtime by stage; and
- run-to-run variance.

## Milestone 37 — End-to-end paper experiment

Evaluate the complete mock pipeline across all three sites and selected backpacks.

Report:

- fields filled by measurement, documentation, and pilots;
- conflicts detected and selected source;
- number and type of unresolved fields;
- profile completeness;
- preflight success, early failure, and remediation accuracy;
- pilot count and mocked cost metadata; and
- total and per-stage runtime and provider usage.

Produce machine-readable result tables suitable for paper analysis. Do not manually edit generated
metrics.

---

## Phase I — Live-site implementation, only after laptop approval

## Milestone 38 — Live common login measurements

Implement safe scheduler-independent measurements from the reviewed common field catalog. Use
fixed commands and parsers; never execute model-generated shell commands.

## Milestone 39 — Live Slurm measurements

Implement fixed Slurm command adapters and parsers for the reviewed Slurm catalog. Permission
failures and unavailable commands become explicit observation states rather than fatal pipeline
errors.

## Milestone 40 — Live HTCondor measurements

Implement fixed HTCondor command adapters and ClassAd normalization for the reviewed HTCondor
catalog. Derive resource groups deterministically and never synthesize partitions.

## Milestone 41 — Live approved pilot jobs

Implement scheduler-specific templates only for pilots in the approved registry. Enforce job,
time, network, candidate-port, and retry budgets. Require explicit authorization before
submission.

## Milestone 42 — Mock/live agreement and release gate

Capture live evidence for approved evaluation runs and compare it with normalized mock fixtures.

Verify:

- mock and live providers produce the same contracts;
- reconciliation gives explainable differences when evidence changed;
- safety limits hold;
- performance artifacts are complete on success and failure; and
- no workflow is automatically submitted, deployed, or resubmitted.

Only after this gate should the implementation be considered for promotion into a production or
separate real-site repository.
