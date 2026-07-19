# HPC Site Preflight

HPC Site Preflight constructs an evidence-backed profile of an HPC system and uses that profile to determine whether a portable workflow package can be deployed safely.

The working machine-readable site-profile contract, field semantics, evidence boundary, and a
full illustrative example are described in [docs/SITE_PROFILE.md](docs/SITE_PROFILE.md).
Common scheduler-independent login-node observations and their safety boundary are defined in
[docs/MEASUREMENT_FIELDS.md](docs/MEASUREMENT_FIELDS.md).

The project combines four evidence sources:

1. existing site profiles;
2. login-node measurements;
3. official documentation analyzed with bounded AI assistance;
4. predefined pilot-job results.

AI is restricted to documentation discovery and structured extraction. Measurements, pilot scripts, evidence validation, reconciliation, and the final preflight decision remain deterministic.

## Project status

The deterministic foundation and first measurement-only profile builder are complete:

- an installable `src/` package;
- a working CLI and command hierarchy;
- run-level and step-level performance tracking;
- typed site, measurement, profile, and evidence contracts;
- simulated Anvil, Stampede3, and Notre Dame CRC inputs;
- measurement-only partial profile construction;
- tests;
- explicit `NotImplementedError` messages for unfinished stages.

See [MILESTONES.md](MILESTONES.md) for the planned sequence of small implementation prompts.

## Main workflows

### Build a site profile

A backpack is not required.

```text
site information
→ profile lookup
→ measurements
→ documentation evidence
→ approved pilot results
→ reconciliation
→ site profile
```

### Preflight a portable workflow

```text
backpack + site profile
→ compatibility checks
→ execution plan or early failure
```

### Evaluate only the AI/documentation subsystem

```text
site information
→ documentation discovery
→ corpus
→ context selection
→ extraction
→ evidence validation
```

The planned context modes are `full-corpus`, `bm25`, and `schema-expanded-bm25`.

## Simulate and live modes

- **Simulate mode** is the default. It requires site-information and measurement files and never
  queries the current hardware.
- **Live mode** reuses supplied inputs and will measure any missing inputs on the HPC login node.
  Pilot jobs remain explicitly authorized operations.
- Evidence records their source as **simulated** or **measured**.

Both modes must feed the same normalized evidence interfaces so that downstream policy construction behaves identically.

## Installation

```bash
conda env create -f environment.yml
conda activate hpc-site-preflight
```

Or with an existing Python environment:

```bash
python -m pip install -e ".[dev]"
```

## CLI

```bash
hpc-site-preflight --help
hpc-site-preflight profile build --help
hpc-site-preflight profile validate --help
hpc-site-preflight profile show --help
hpc-site-preflight evidence capture-login --help
hpc-site-preflight evidence run-pilots --help
hpc-site-preflight evaluate documentation --help
hpc-site-preflight preflight --help
```

`profile build` currently constructs measurement-only partial profiles in simulate mode. Other
unfinished commands create run reports and fail explicitly.

## Performance reporting

Every command creates one tracker and passes it through the pipeline. The tracker aggregates:

- total runtime;
- runtime per pipeline step;
- model requests;
- input and output tokens;
- retries;
- tool calls;
- produced artifacts;
- failed-step information.

Reports are written to:

```text
runs/<run-id>/performance.json
runs/<run-id>/trace.jsonl
```

Usage is reported per pipeline step and for the whole run. Missing provider token data is recorded as unavailable rather than estimated.

## Repository relationship

The existing `hpc-site-policy-agent` repository is the reference implementation for the future documentation subsystem. Its discovery, scope filtering, corpus, retrieval, extraction, and evidence validation code should be adapted behind `DocumentationPolicyProvider`; its top-level CLI and control loop should not be copied into this project.

## Trust boundary

The project must not allow an LLM to:

- execute arbitrary shell commands;
- generate unrestricted network tests;
- submit unapproved pilot jobs;
- directly certify a site policy;
- approve or launch a real workflow;
- silently resolve conflicting evidence.

See [AGENTS.md](AGENTS.md) for authoritative implementation rules.
