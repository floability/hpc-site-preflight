# Coding Agent Guide

## Purpose

HPC Site Preflight fills the site-specific half of portable workflow deployment. Portable packages describe what a workflow needs; this project constructs a reusable description of what an HPC site requires and permits, then performs deterministic preflight checks.

## Architecture boundary

The system has four layers:

1. **Evidence acquisition** — profile lookup, measurements, documentation analysis, and approved pilots.
2. **Evidence validation** — schema, provenance, scope, and evidence-consistency checks.
3. **Policy construction** — deterministic reconciliation into a compact site profile.
4. **Preflight planning** — deterministic compatibility checks and execution-plan generation.

AI may assist documentation discovery and extraction. It must not perform measurement, write arbitrary probes, reconcile conflicts, or approve deployment.

## Execution modes

- `fixture`: reviewed JSON measurements and pilot results for laptop development. Each fixture
  declares whether its origin is captured, curated, or illustrative.
- `live`: real login-node measurements and approved pilot jobs.
- documentation evaluation: stops after documentation-derived partial policy construction.

Fixture and live implementations must conform to the same provider interfaces.

## Authoritative files

- `README.md`: user-facing overview.
- `MILESTONES.md`: implementation sequence and acceptance criteria.
- `docs/ARCHITECTURE.md`: component boundaries.
- `docs/PIPELINE.md`: end-to-end stages.
- `docs/MIGRATION_FROM_POLICY_AGENT.md`: reusable code from the existing documentation project.
- `SKILL.md`: short pointer for LLM tools; do not duplicate detailed rules there.

## Coding rules

- Use Python 3.11+ and a `src/` layout.
- Use type hints throughout.
- Prefer small modules and dependency injection.
- Use `pathlib.Path` for filesystem work.
- Use Pydantic models for external JSON contracts.
- Raise explicit project exceptions.
- Never return fake successful results from unfinished code.
- Every unfinished public operation must raise `FeatureNotImplementedError` with a specific message.
- Tests must not require live HPC access or API keys unless explicitly marked as integration tests.
- Do not replace the project with LangChain, LangGraph, CrewAI, AutoGen, or another orchestration framework.

## Performance tracking

Create one `RunTracker` per command and pass it through all stages. Each stage must use:

```python
with tracker.stage("stage_name"):
    ...
```

Provider adapters must report model usage through `tracker.record_model_usage(...)`. Tools and retry handlers must report through the corresponding tracker methods.

The tracker reports after every stage and writes aggregated `performance.json` and `trace.jsonl` artifacts, including on failure. Never log secrets or full downloaded page bodies in normal traces.

## Site-profile lookup order

1. explicit local `--profile`;
2. explicit HTTPS `--profile-url`;
3. `~/.hpc-site-preflight/profiles/<site-id>.json`;
4. optional `~/.floability/site-profiles/<site-id>.json` compatibility path.

Remote profiles are candidates until validated locally.

## Scheduler support

Slurm and HTCondor are first-class targets.

Slurm measurements may include partitions, node shapes, accounts, QoS, and visible configuration.

HTCondor does not have Slurm partitions. Summarize observable pool structure from machine ClassAd attributes such as slot type, machine family, architecture, memory, CPU, disk, GPU model, and administrator-defined grouping attributes. These are resource groups, not administrative partitions.

## Documentation subsystem

Adapt the existing `hpc-site-policy-agent` code behind the interface in `documentation/base.py`. Preserve:

- bounded discovery;
- domain allowlists;
- target-site and sibling-site scope filtering;
- normalized persistent corpus;
- heading-aware chunking and table preservation;
- BM25 and schema-expanded BM25 modes;
- constrained extraction;
- evidence validation;
- detailed provenance.

Do not copy its top-level control loop.

## Safety restrictions

Do not implement:

- arbitrary model-generated shell commands;
- unrestricted port scanning;
- automatic workflow deployment or resubmission;
- silent conflict resolution;
- use of sibling-site documentation as target-site policy;
- undocumented port ranges;
- token estimates presented as provider-reported usage.

## Workflow for each milestone

1. Read the relevant milestone in `MILESTONES.md`.
2. Inspect current tests and interfaces.
3. Implement only that milestone.
4. Add or update tests.
5. Run `pytest`.
6. Run `python -m compileall src`.
7. Report behavior changes and unresolved items.
8. Do not begin the next milestone automatically.
