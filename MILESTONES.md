# HPC Site Preflight Milestones

Build evidence-backed HPC site profiles, evaluate how they are constructed, and use them for
deterministic preflight before submission.

## Research questions

- **RQ1:** Can the system discover authoritative documentation and construct accurate,
  evidence-backed fields while abstaining where the site is silent?
- **RQ2:** How do retrieval strategies trade coverage and correctness against token cost, latency,
  and run-to-run variance?
- **RQ3:** Do constructed profiles catch incompatibilities before submission, and at what
  false-rejection rate?

## Execution contract

- Site, model, and web modes are independent; site simulation never queries laptop hardware.
- Live site mode reuses supplied measurements or captures them from the login node.
- AI is limited to documentation discovery, query expansion, and typed extraction.
- Pilot submission always requires explicit authorization.

## Phase A — Implemented foundation

### Milestone 1 — Evidence and profile contracts

**Status: Completed**

Define typed login measurements, evidence records, simulated sites, and the partial site profile.

- Implemented Slurm and HTCondor contracts, three simulated sites, and measurement-backed profiles.

**Test:** Validate every example through its Pydantic model and build all simulated profiles.

### Milestone 2 — Bounded documentation agent

**Status: Completed**

Discover official target-site pages without giving the model unrestricted web access.

- Implemented deterministic DuckDuckGo search, bounded fetch tools, domain checks, and one AI
  source-selection step.

**Test:** Replay successful, partial, out-of-scope, and failed-fetch discovery cases.

### Milestone 3 — Corpus, retrieval, and extraction

**Status: Completed**

Turn official pages into exact evidence spans and typed profile-field candidates.

- Implemented persistent corpora, batched full corpus, BM25, additive-query BM25, typed extraction,
  citation validation, and bounded correction.

**Test:** Rebuild a corpus deterministically and run all three context modes offline.

### Milestone 4 — End-to-end profile build

**Status: Completed**

Run measured or simulated site evidence through live or recorded documentation analysis.

- Implemented independent execution modes, progress traces, provider usage, and profile artifacts.

**Test:** Reproduce the three offline site profiles and validate every evidence link.

## Phase B — Evidence-safe AI results

### Milestone 5 — Abstention and canonical extraction

**Status: Incomplete**

Prevent unsupported findings and make model outputs match the profile contract directly.

- Reject absence-as-false and indirect networking inferences.
- Constrain and normalize scheduler option and storage names.
- Preserve valid fields while correcting only invalid fields once.

**Test:** Anvil fills documented submission fields while silent network fields remain `null`.

### Milestone 6 — Authoritative discovery coverage

**Status: Needs more work**

Find the small set of official pages needed for every profile topic.

- Balance selection across submission, resources, filesystem storage, networking, and operations.
- Record topic gaps and distinguish filesystem policy from object-storage documentation.
- Verify target-site scope and authority for every selected page.

**Test:** Anvil discovery retrieves the documented partition, charging, scratch, and project rules.

### Milestone 7 — Controlled retrieval comparison

**Status: Incomplete**

Evaluate retrieval modes against an identical frozen corpus.

- Preserve base BM25 hits and append expanded-query hits.
- Load a captured corpus without rerunning discovery.
- Record field coverage, correctness, tokens, latency, and repeated-run variance.

**Test:** Reproduce all RQ2 modes from one corpus fingerprint with one evaluation command.

## Phase C — Complete site evidence

### Milestone 8 — Live scheduler collection

**Status: Partially completed**

Finish safe login-node collection for both scheduler families.

- Complete reviewed Slurm parsing and HTCondor ClassAd resource groups.
- Record unavailable observations rather than failing collection.
- Validate real Anvil, Stampede3, and Notre Dame CRC captures.

**Test:** Parse recorded command outputs before running marked live-site integrations.

### Milestone 9 — Approved pilot evidence

**Status: Incomplete**

Measure unresolved compute-node behavior with predefined, explicitly approved jobs.

- Add simulated and live pilot contracts for networking and compute-visible storage.
- Preserve approval, queue time, result status, and exact pilot provenance.
- Never generate arbitrary commands or submit automatically.

**Test:** Replay every pilot and prove that live submission cannot occur without authorization.

### Milestone 10 — Deterministic reconciliation

**Status: Incomplete**

Combine measurement, documentation, and pilots without silent conflict resolution.

- Apply field-specific authority and freshness rules.
- Preserve conflicts, abstentions, selected evidence, and next actions.
- Validate the final profile and evidence report together.

**Test:** Cover agreement, conflict, stale evidence, silence, and missing evidence.

## Phase D — Preflight and paper evaluation

### Milestone 11 — Workflow preflight

**Status: Incomplete**

Compare portable workflow requirements with a validated site profile before submission.

- Check scheduler, submission, resources, storage, software, and networking deterministically.
- Emit an execution plan, an unknown-policy result, or an early failure with remediation.
- Never deploy or resubmit a workflow automatically.

**Test:** Cover runnable, remediable, blocked, and unknown-policy workflows.

### Milestone 12 — Reproducible RQ evaluation

**Status: Incomplete**

Produce the evidence needed to answer RQ1, RQ2, and RQ3.

- Maintain reviewed field truth and authoritative citations separately from development fixtures.
- Measure accuracy, abstention, citation validity, cost, latency, variance, and profile completeness.
- Inject workflow incompatibilities and report detection and false-rejection rates.

**Test:** Reproduce every paper table from versioned inputs with documented commands.
