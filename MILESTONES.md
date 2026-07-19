# HPC Site Preflight Milestones

Build the smallest complete path from site evidence to an evidence-backed profile, then add live
collection, pilots, and workflow preflight.

## Execution contract

- `simulate` is the default mode; `--site-info` and `--measurements` are required and hardware is
  never queried.
- `live` reuses supplied inputs and eventually measures any missing inputs on the real login node.
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

Validate site information and flat measurement evidence with simple external contracts.

- Added site-info, measurement, observation, and scheduler-specific models.
- Added `simulated | measured` evidence-source validation.

**Test:** Load all example JSON through the Pydantic models.

## Phase B — Simulated sites

### Milestone 5 — Three site simulations

**Status: Completed**

Provide laptop inputs for Anvil, Stampede3, and Notre Dame CRC.

- Added site information and simulated measurements for two Slurm sites and one HTCondor site.
- Preserved observable facts as evidence, including Anvil's visible infinite walltime.

**Test:** Validate every pair under `examples/simulate/`.

### Milestone 6 — Simulated provider

**Status: Completed**

Load simulated evidence without touching the current hardware.

- Added the simulated measurement provider with site and scheduler checks.
- Made `simulate` the default profile-build mode.

**Test:** Build a profile from each simulated site.

## Phase C — First partial profile

### Milestone 7 — Measurement-only profile

**Status: Completed**

Produce a useful partial site profile and detailed evidence report without failing on unknowns.

- Added deterministic compilation, evidence links, and unresolved actions.
- Added profile and evidence artifacts for Slurm and HTCondor simulations.

**Test:** Run `profile build` for all three sites and inspect both output files.

## Phase D — Documentation AI building blocks

### Milestone 8 — Model provider and structured calls

**Status: Next**

Create the smallest provider-neutral interface needed for schema-constrained AI results.

- Define one model request/response contract and a basic OpenAI adapter.
- Require structured JSON output and validate it locally.
- Record provider-reported usage, retries, and latency with `RunTracker`.
- Test offline with recorded responses; API tests remain optional integrations.

**Test:** Parse valid, invalid, and partial recorded model responses without an API key.

### Milestone 9 — Site identity and query plan

**Status: Incomplete**

Turn site information and measurements into deterministic documentation search inputs.

- Normalize site name, aliases, scheduler, hostname patterns, and allowed domains.
- Build a small fixed set of policy-oriented search queries.
- Keep organization-wide and sibling-site material separate from target-site policy.

**Test:** Snapshot the query plans for all three simulated sites.

### Milestone 10 — Bounded search and fetch tools

**Status: Incomplete**

Expose only the reviewed tools needed for documentation discovery.

- Implement `search_web`, `fetch_page`, and `finish_discovery` behind small interfaces.
- Enforce HTTPS, domain allowlists, page limits, timeouts, and content-size limits.
- Record URLs and hashes without writing full page bodies to normal traces.

**Test:** Use recorded search results and pages to verify every bound and rejection.

### Milestone 11 — Bounded discovery agent

**Status: Incomplete**

Let one agent find useful official pages while deterministic code controls its scope and budget.

- Give the model only the three discovery tools and a fixed turn limit.
- Validate every proposed tool action before execution.
- Preserve partial discoveries when the budget ends or a page fails.
- Reject sibling-site pages as target-site evidence.

**Test:** Replay successful, partial, out-of-scope, and budget-exhausted discovery runs.

### Milestone 12 — Normalized document corpus

**Status: Incomplete**

Convert fetched official pages into a persistent corpus suitable for repeatable extraction.

- Preserve headings, section paths, tables, source URLs, and content hashes.
- Create stable document and chunk identifiers.
- Store normalized records in JSONL for reuse across context modes.

**Test:** Rebuild the same recorded corpus twice and compare identifiers and hashes.

### Milestone 13 — Three context modes

**Status: Incomplete**

Select extraction context using full corpus, BM25, or schema-expanded BM25.

- Implement the three modes over the same normalized chunks.
- Use deterministic token and chunk limits.
- Keep selected chunk IDs so retrieval experiments are reproducible.

**Test:** Run all modes on one corpus and verify stable selected chunks.

### Milestone 14 — Evidence-span extraction

**Status: Incomplete**

Extract typed field candidates that point to exact local evidence spans.

- Give each sentence or table row a stable span ID before the model call.
- Extract small policy groups with nullable fields and selected span IDs.
- Resolve quotes locally; discard unknown, altered, or out-of-scope spans.
- Allow one constrained correction attempt, then keep the field empty.

**Test:** Replay valid, unsupported, misquoted, and absent-field responses.

### Milestone 15 — Documentation policy result

**Status: Incomplete**

Produce a documentation-derived partial policy with evidence for every accepted field.

- Combine validated extraction groups without model-driven reconciliation.
- Emit accepted, rejected, and unresolved fields with provenance.
- Never turn sibling-site or organization-wide guidance into target-site policy.
- Preserve empty values instead of failing the run.

**Test:** Build documentation results for each site and validate every evidence link.

### Milestone 16 — End-to-end simulated AI profile

**Status: Incomplete**

Run the documentation pipeline from simulated inputs to a partial site profile.

- Connect identity, discovery, corpus, retrieval, extraction, and profile compilation linearly.
- Support the three context modes from the CLI.
- Use recorded web and model responses for the default offline tests.
- Write the profile, evidence report, corpus, performance report, and trace.

**Test:** Reproduce all three site profiles offline and compare deterministic artifacts.

## Phase E — Live evidence and pilots

### Milestone 17 — Live input resolver

**Status: Incomplete**

Allow real-site runs to reuse supplied inputs and collect only what is missing.

- Keep both files required in `simulate` mode.
- In `live` mode, load supplied site information and measured evidence when present.
- Derive missing site information and run fixed login-node collectors when absent.
- Save newly measured inputs before continuing through the same pipeline.

**Test:** Verify supplied, missing, and mixed-input cases with collector fakes.

### Milestone 18 — Live Slurm and HTCondor collectors

**Status: Incomplete**

Collect the reviewed measurement catalogs safely on real login nodes.

- Use only fixed argument arrays and bounded collector functions.
- Record unavailable values instead of aborting the bundle.
- Normalize Slurm and HTCondor output into the existing flat contract.
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
