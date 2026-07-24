# HPC Site Preflight Milestones

Build the smallest complete path from site evidence to an evidence-backed profile, then add live
collection, pilots, and workflow preflight.

## Execution contract

- Site, model, and web modes are independent.
- Site mode defaults to `simulate`; a supplied login-measurement file replaces real HPC access.
- Model and web modes default to `live`; their `simulate` modes replay offline recordings.
- Live site mode reuses supplied measurements or captures them on the real login node.
- Evidence source is `simulated` or `measured`, independent of execution mode.
- Pilot jobs are never automatic and always require explicit authorization.

## Phase A — Foundations

### Milestone 1 — Project skeleton

**Status: Completed**

Provide a runnable package with explicit boundaries and observable command execution.

- Added the CLI, typed placeholders, project exceptions, and performance tracking.
- Added offline tests and explicit failures for unfinished commands.

**Test:** Run `hpc-site-preflight --help` and `pytest`.

### Milestone 2 — Common login facts

**Status: Completed**

Define scheduler-independent facts that are safe to observe from a login node.

- Added a compact common field catalog with fixed acquisition methods.
- Kept compute-node, batch-job, and unrestricted network facts out of scope.

**Test:** Validate `schemas/measurement-fields/common.json` and its catalog test.

### Milestone 3 — Scheduler facts

**Status: Completed**

Define safely observable Slurm and HTCondor login-node facts.

- Added reviewed Slurm commands and derived partition and node facts.
- Added HTCondor ClassAd and resource-group facts without inventing partitions.

**Test:** Validate both scheduler catalogs and their command allowlists.

### Milestone 4 — Evidence contracts

**Status: Completed**

Validate the structured login-measurement document as the single site input.

- Added structured site, storage, Slurm, HTCondor, and measurement-bundle models.
- Added `simulated | measured` evidence-source validation.

**Test:** Load all example JSON through the Pydantic models.

## Phase B — Simulated sites

### Milestone 5 — Three site simulations

**Status: Completed**

Provide laptop inputs for Anvil, Stampede3, and Notre Dame CRC.

- Added structured simulated measurements for two Slurm sites and one HTCondor site.
- Preserved observable facts as evidence, including Anvil's visible infinite walltime.

**Test:** Validate every pair under `examples/simulate/`.

### Milestone 6 — Simulated provider

**Status: Completed**

Load simulated evidence without touching the current hardware.

- Added the simulated measurement provider with contract and scheduler-consistency checks.
- Made simulated site inputs independent from model and web execution.

**Test:** Build a profile from each simulated site.

## Phase C — First partial profile

### Milestone 7 — Measurement-only profile

**Status: Completed**

Produce a useful partial site profile and detailed evidence report without failing on unknowns.

- Added deterministic compilation, evidence links, and unresolved actions.
- Added `0.2` storage path patterns and structured login/compute networking.

**Test:** Run `profile build` for all three sites and inspect both output files.

## Phase D — Documentation AI building blocks

### Milestone 8 — Model provider and structured calls

**Status: Completed**

Create the smallest provider-neutral interface needed for schema-constrained AI results.

- Added a provider-neutral typed-result interface with a basic OpenAI adapter.
- Added model-to-provider inference for OpenAI and future Anthropic and Gemini adapters.
- Added local validation, retry tracking, usage reporting, and offline recordings.

**Test:** Parse valid, invalid, and partial recorded model responses without an API key.

### Milestone 9 — Site identity and query plan

**Status: Completed**

Turn measured site facts into deterministic documentation search inputs.

- Added normalized identity from site, scheduler, hostname, and domain measurement fields.
- Added four reproducible policy queries and deterministic source scope.

**Test:** Snapshot the query plans for all three simulated sites.

### Milestone 10 — Bounded search and fetch tools

**Status: Completed**

Expose only the reviewed tools needed for documentation discovery.

- Added bounded live and recorded search/fetch tools over one interface.
- Added HTTPS, domain, budget, size, timeout, and body-free trace controls.

**Test:** Use recorded search results and pages to verify every bound and rejection.

### Milestone 11 — Bounded discovery agent

**Status: Completed**

Let one agent find useful official pages while deterministic code controls its scope and budget.

- Added one discovery agent with bounded search and download tools.
- Added one structured source-selection call, one correction bound, partial fallback, and sibling
  rejection.

**Test:** Replay successful, partial, out-of-scope, and budget-exhausted discovery runs.

### Milestone 12 — Normalized document corpus

**Status: Completed**

Convert fetched official pages into a persistent corpus suitable for repeatable extraction.

- Added heading-aware, table-preserving records with stable IDs and hashes.
- Added persistent manifest, document JSONL, and chunk JSONL artifacts.

**Test:** Rebuild the same recorded corpus twice and compare identifiers and hashes.

### Milestone 13 — Three context modes

**Status: Completed**

Select extraction context using full corpus, BM25, or LLM-expanded BM25.

- Added bounded full-corpus batches, BM25, and additive LLM-expanded BM25 selection.
- Expanded BM25 retains base queries, adds bounded variants, and uses deterministic scoring.

**Test:** Verify full-corpus batches cover every chunk once and ranked modes select stable chunks.

### Milestone 14 — Evidence-span extraction

**Status: Completed**

Extract typed field candidates that point to exact local evidence spans.

- Added exact local spans, three group schemas, and one combined full-corpus batch schema.
- Added independent resource, scope, and citation validation with one bounded correction.

**Test:** Replay valid, unsupported, misquoted, and absent-field responses.

### Milestone 15 — Documentation policy result

**Status: Completed**

Produce a documentation-derived partial policy with evidence for every accepted field.

- Added canonical accepted findings plus rejected and unresolved results.
- Added direct typed profile application and exact evidence-report provenance.

**Test:** Build documentation results for each site and validate every evidence link.

### Milestone 16 — End-to-end live and replayable AI profile

**Status: Completed**

Run real or recorded documentation AI from simulated site inputs to a partial site profile.

- Defaulted to live model and web modes while keeping site simulation independent.
- Kept recorded web/model modes for deterministic offline tests.

**Test:** Reproduce all three site profiles offline and compare deterministic artifacts.

## Phase E — Live evidence and pilots

### Milestone 17 — Live input resolver

**Status: Completed**

Use one structured measurement file for both laptop simulation and real-site capture.

- Simulated site mode requires `--measurements` and never reads current hardware.
- Live site mode loads a supplied file or captures and saves one when it is absent.
- Added `evidence capture-login` for measurement-only collection.

**Test:** Verify supplied, missing, and mixed-input cases with collector fakes.

### Milestone 18 — Live Slurm and HTCondor collectors

**Status: Partially completed**

Collect the reviewed measurement catalogs safely on real login nodes.

- Use only fixed argument arrays and bounded collector functions.
- Record unavailable values instead of aborting the bundle.
- Normalize values into the structured `0.6` contract.
- Mark the resulting evidence source as `measured`.

**Test:** Parse recorded command outputs, then run explicitly marked site integrations.

### Milestone 19 — Approved pilots

**Status: Incomplete**

Fill selected documentation gaps with predefined simulated or explicitly approved pilot jobs.

- Define a small pilot catalog for network and compute-node facts.
- Add simulated pilot results for laptop development.
- Require explicit authorization before any live submission.
- Preserve queue time, result status, and pilot provenance.

**Test:** Replay each pilot result and verify that live submission is never implicit.

### Milestone 20 — Deterministic reconciliation

**Status: Incomplete**

Combine measurement, documentation, pilot, and user evidence without silent conflict resolution.

- Define field-level precedence and freshness rules in code.
- Record conflicts, selected evidence, and the applied rule.
- Keep unresolved fields empty with a concrete next action.
- Validate the final profile and evidence report together.

**Test:** Cover agreement, conflict, stale evidence, and missing evidence cases.

## Phase F — Preflight and paper evaluation

### Milestone 21 — Workflow preflight

**Status: Incomplete**

Compare workflow requirements with a site profile and produce an actionable result.

- Load a minimal Backpack requirement contract.
- Check scheduler, submission, resources, storage, software, and networking deterministically.
- Emit an execution plan or an early failure with remediation.
- Never deploy or resubmit a workflow automatically.

**Test:** Cover runnable, remediable, blocked, and unknown-policy workflows.

### Milestone 22 — Evaluation harness

**Status: Incomplete**

Measure the paper's discovery quality, evidence quality, completeness, cost, and reproducibility.

- Evaluate Anvil, Stampede3, and Notre Dame CRC across all context modes.
- Report field precision, recall, abstention, citation validity, and profile completeness.
- Record model usage, wall time, tool calls, pilot count, and run-to-run variation.
- Keep reviewed ground truth separate from simulated development inputs.

**Test:** Reproduce tables from versioned inputs with one documented command.
