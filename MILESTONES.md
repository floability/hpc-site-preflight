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

Development is fixture-first and laptop-first.

- `fixture` mode uses reviewed JSON fixtures for HPC login measurements and pilot results. It must
  not require access to an HPC system.
- Documentation tests use captured pages, corpora, and provider responses by default. Explicitly
  marked integration runs may use approved web access and model APIs.
- `live` mode is deferred until the complete fixture pipeline works for all three evaluation sites.
- Fixture and live implementations must eventually conform to the same provider interfaces and
  external JSON contracts.

Fixture evidence represents the shape and behavior of evidence that will later be captured live;
it must not return invented success values from unfinished operations. Every fixture declares an
origin of `captured`, `curated`, or `illustrative` so development examples cannot be mistaken for
authoritative measurements.

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

## Status legend

Statuses describe the repository state as of July 18, 2026:

- **Completed:** the milestone acceptance criteria are implemented and verified.
- **Partially completed:** substantive milestone artifacts or implementation exist in this
  repository, but the acceptance criteria are not fully met.
- **Needs more work:** design notes, skeletons, or reusable prototype work exist, but the core
  milestone deliverable is not yet usable in this repository.
- **Incomplete:** no substantive milestone implementation exists beyond planning or explicit
  placeholders.

---

## Phase A — Foundation

## Milestone 1 — Package, CLI, and performance tracker

**Status:** Completed

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

These are the next milestones. The field catalogs and fixture files are reviewed before measurement
loaders or collectors are implemented. They become the source material for the typed contracts.

## Milestone 2 — Common login-node measurement field catalog

**Status:** Completed

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

## Milestone 3 — Slurm measurement catalog

**Status:** Completed

Define Slurm-specific facts that can be observed from the login node.

Include:

* cluster and Slurm version;
* partitions and default partition;
* partition availability and state;
* node counts and states;
* CPU, memory, temporary disk, features, and node shapes;
* GRES and GPU information;
* visible accounts, QoS, reservations, and limits;
* useful scheduler configuration values;
* unavailable, hidden, permission-denied, and missing-command results.

For each field, record the likely Slurm command and output source.

Do not treat visible configuration as enforced policy. In particular, preserve cases where a displayed walltime limit differs from the actual accepted limit.

**Acceptance:**

* fields use the common evidence format;
* partitions and node shapes remain separate;
* configuration is labeled as observation, not policy.

---

## Milestone 4 — HTCondor measurement catalog

**Status:** Completed

Define HTCondor-specific facts visible from collectors, schedulers, and machine ClassAds.

Include:

* collector and schedd identity;
* HTCondor and pool version;
* execute machines and slot counts;
* static, partitionable, and dynamic slots;
* slot state and activity;
* CPU, memory, disk, GPU, OS, and architecture;
* machine grouping attributes;
* observable submission capabilities;
* unavailable, hidden, permission-denied, and missing-command results.

Define how resource groups may be derived from selected ClassAd attributes.

Do not call HTCondor resource groups partitions.

**Acceptance:**

* fields use the common evidence format;
* raw ClassAds and derived groups remain separate;
* Slurm-only fields are excluded.

---

## Milestone 5 — Site and measurement JSON structure

**Status:** Completed

Finalize the shared JSON structure for site information and measurement fixtures.

Define:

* `site-info.json`;
* the measurement bundle;
* schema version;
* site ID and scheduler;
* collection timestamps;
* source mode and fixture origin;
* collector version;
* observation status and provenance.

Use:

```text
source_mode: fixture | live
fixture_origin: captured | curated | illustrative
```

Unknown values must remain different from `false`, `0`, or an empty list.

**Acceptance:**

* one structure supports Slurm and HTCondor;
* scheduler-specific data stays inside its scheduler section;
* unavailable and permission-denied values are explicit.

---

## Milestone 6 — Purdue Anvil fixture

**Status:** Completed

Create:

```text
examples/fixture/anvil/site-info.json
examples/fixture/anvil/login-measurements.json
```

Include:

* representative partitions;
* representative node shapes;
* the visible walltime value used in the motivating conflict example;
* provenance for every value.

Mark values as captured, curated, illustrative, or unresolved.

Do not treat illustrative values as current policy.

---

## Milestone 7 — Stampede3 fixture

**Status:** Completed

Create:

```text
examples/fixture/stampede3/site-info.json
examples/fixture/stampede3/login-measurements.json
```

Use the same Slurm structure as Anvil.

Site differences should appear only in values, not in new site-specific fields or Python classes.

---

## Milestone 8 — Notre Dame CRC fixture

**Status:** Completed

Create:

```text
examples/fixture/notre-dame-crc/site-info.json
examples/fixture/notre-dame-crc/login-measurements.json
```

Use the common and HTCondor structures.

Include:

* representative machines;
* representative slot attributes;
* at least one derived resource group.

Do not use `partitions` for HTCondor.

---

## Milestone 9 — Typed measurement models

**Status:** Completed

Implement Pydantic models for:

* site information;
* common measurements;
* Slurm measurements;
* HTCondor measurements;
* observation status;
* provenance;
* schema version.

Tests should:

* validate all three fixtures;
* reject Slurm fields in HTCondor data;
* reject HTCondor fields in Slurm data.

Do not build a site profile yet.

---

## Milestone 10 — Fixture measurement provider

**Status:** Completed

Implement a provider that loads measurement fixtures from disk.

Requirements:

* use the shared `MeasurementProvider` interface;
* validate the fixture;
* verify site ID and scheduler against `site-info.json`;
* record loading and validation as separate tracker stages;
* preserve provenance;
* use the word `fixture` consistently in code, CLI, examples, and tests.

Do not execute shell commands or build a site profile.


---

## Phase C — Formalize the profile and evidence contracts

## Milestone 11 — Actionable site-profile schema

**Status:** Completed

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

**Status:** Completed

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

**Status:** Completed

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

**Status:** Completed

Construct the first useful partial site profile from validated fixture login measurements only.

Requirements:

- populate fields that measurements can establish;
- leave policy-only and pilot-only fields `null`;
- emit unresolved work items instead of failing for missing fields;
- write a compact profile and detailed evidence report; and
- support Anvil, Stampede3, and Notre Dame CRC with one code path.

This milestone deliberately excludes documentation and pilot evidence.

---

## Phase D — Documentation evidence on a laptop

## Milestone 15 — Documentation provider contract and fixture result

**Status:** Partially completed

Finalize `DocumentationPolicyProvider` inputs and outputs and create a validated fixture
documentation result for one site.

The result must contain a partial policy, detailed field evidence, unresolved documentation
questions, context mode, corpus identity, and provider-reported usage availability.

Do not migrate discovery yet.

## Milestone 16 — Prototype migration inventory

**Status:** Partially completed

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

**Status:** Needs more work

Adapt the bounded search/fetch/finish discovery behavior behind the documentation provider.

Requirements:

- approved-domain enforcement;
- search, page, step, timeout, and cost budgets;
- local validation of every requested action and URL;
- deterministic fallback to partial discovery state; and
- tracker stages for discovery and provider usage.

Offline tests use recorded search and page fixtures. No unrestricted browsing is allowed.

## Milestone 18 — Site scope, trust, and page normalization

**Status:** Needs more work

Implement deterministic target-site, organization-general, sibling-site, and unrelated scope
classification. Keep scope independent of trust.

Fetch and normalize readable target-site content while preserving URL, title, headings, tables,
timestamps, hashes, and scope. Sibling pages may be retained as negative controls but cannot be
used as target-site policy.

## Milestone 19 — Persistent corpus and chunking

**Status:** Needs more work

Implement the content-hashed persistent corpus:

- manifest, documents, and chunks;
- canonical URLs and content hashes;
- heading-aware chunks;
- atomic Markdown tables;
- intact policy and FAQ sections where appropriate;
- deterministic refresh behavior; and
- no persisted vector index.

## Milestone 20 — Full-corpus context mode

**Status:** Incomplete

Implement deterministic `full-corpus` context construction, size accounting, truncation rules,
and field coverage reporting.

Do not implement BM25 in this milestone.

## Milestone 21 — BM25 context mode

**Status:** Needs more work

Implement transient CPU-only BM25 retrieval with field-specific queries, scope filtering before
ranking, local deduplication, and retrieved-but-uncited tracking.

Do not implement expanded queries in this milestone.

## Milestone 22 — Schema-expanded BM25 context mode

**Status:** Needs more work

Implement deterministic schema-derived query expansion on top of the same BM25 index. Preserve
base and expanded query provenance so the paper can compare retrieval behavior.

## Milestone 23 — Constrained documentation extraction

**Status:** Needs more work

Implement schema-constrained extraction that proposes typed field values and selects field-local
evidence span IDs.

Python, not the model, inserts exact quotes, URLs, headings, chunk IDs, and provenance. Provider
adapters must report only provider-supplied token usage.

## Milestone 24 — Field-level documentation evidence validation

**Status:** Needs more work

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

**Status:** Incomplete

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

## Phase E — Fixture pilots and deterministic reconciliation

## Milestone 26 — Approved pilot catalog

**Status:** Needs more work

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

## Milestone 27 — Fixture pilot results and provider

**Status:** Partially completed

Create typed pilot-result fixtures for the three evaluation sites and implement a fixture
provider that loads them through the future live-pilot interface.

Verify site ID, scheduler, pilot registry membership, timestamps, and target-field association.
Record loading and validation as tracker stages.

## Milestone 28 — Full deterministic evidence reconciliation

**Status:** Incomplete

Combine profile candidates, fixture measurements, documentation evidence, and fixture pilot results.

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

**Status:** Partially completed

Define and load normalized workflow requirements for scheduler backend, workers, cores, memory,
GPUs, walltime, storage, temporary space, manager-worker networking, worker-worker networking,
and outbound access.

Invalid backpack structure produces a structured validation artifact. It does not trigger site
measurement or documentation work.

## Milestone 30 — Deterministic compatibility checks

**Status:** Needs more work

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

**Status:** Needs more work

For compatible inputs, select documented scheduler option syntax and render a site-specific
execution plan. Preserve semantic option names and selected syntax in the output for auditing.

For incompatible or unresolved required inputs, emit a structured blocked result with concrete
remediation. Never submit or launch the workflow.

---

## Phase G — Complete laptop pipeline

## Milestone 32 — End-to-end fixture profile build

**Status:** Incomplete

Complete `profile build --mode fixture`:

```text
site information
→ fixture login measurements
→ documentation evidence
→ fixture pilot results
→ deterministic reconciliation
→ partial or complete site profile
```

Every stage uses one shared `RunTracker`. The command writes a usable profile and evidence report
when at least one valid field is available, while recording recoverable stage failures and
unresolved fields.

## Milestone 33 — End-to-end fixture preflight scenarios

**Status:** Incomplete

Run backpack preflight from a laptop against fixture-built profiles for:

- one compatible Slurm workflow;
- one early Slurm failure;
- one compatible HTCondor workflow;
- one HTCondor resource-group failure; and
- one blocked result caused by an unresolved field.

Store expected execution-plan or blocked-result fixtures and test them deterministically.

## Milestone 34 — Three-site regression suite

**Status:** Incomplete

Freeze reviewed fixture inputs and expected normalized outputs for Anvil, Stampede3, and Notre Dame
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

**Status:** Incomplete

Define the evaluated site-profile fields, annotation procedure, acceptable evidence, abstention
rules, conflict labels, and adjudication process for the three sites.

Separate fixture construction from ground-truth labels to avoid evaluating the system against its
own generated output.

## Milestone 36 — Documentation context-mode experiment

**Status:** Incomplete

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

**Status:** Incomplete

Evaluate the complete fixture pipeline across all three sites and selected backpacks.

Report:

- fields filled by measurement, documentation, and pilots;
- conflicts detected and selected source;
- number and type of unresolved fields;
- profile completeness;
- preflight success, early failure, and remediation accuracy;
- pilot count and fixture cost metadata; and
- total and per-stage runtime and provider usage.

Produce machine-readable result tables suitable for paper analysis. Do not manually edit generated
metrics.

---

## Phase I — Live-site implementation, only after laptop approval

## Milestone 38 — Live common login measurements

**Status:** Incomplete

Implement safe scheduler-independent measurements from the reviewed common field catalog. Use
fixed commands and parsers; never execute model-generated shell commands.

## Milestone 39 — Live Slurm measurements

**Status:** Incomplete

Implement fixed Slurm command adapters and parsers for the reviewed Slurm catalog. Permission
failures and unavailable commands become explicit observation states rather than fatal pipeline
errors.

## Milestone 40 — Live HTCondor measurements

**Status:** Incomplete

Implement fixed HTCondor command adapters and ClassAd normalization for the reviewed HTCondor
catalog. Derive resource groups deterministically and never synthesize partitions.

## Milestone 41 — Live approved pilot jobs

**Status:** Incomplete

Implement scheduler-specific templates only for pilots in the approved registry. Enforce job,
time, network, candidate-port, and retry budgets. Require explicit authorization before
submission.

## Milestone 42 — Fixture/live agreement and release gate

**Status:** Incomplete

Capture live evidence for approved evaluation runs and compare it with normalized fixtures.

Verify:

- fixture and live providers produce the same contracts;
- reconciliation gives explainable differences when evidence changed;
- safety limits hold;
- performance artifacts are complete on success and failure; and
- no workflow is automatically submitted, deployed, or resubmitted.

Only after this gate should the implementation be considered for promotion into a production or
separate real-site repository.
