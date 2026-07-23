# HPC Site Preflight

HPC Site Preflight constructs an evidence-backed profile of an HPC system and uses that profile to determine whether a portable workflow package can be deployed safely.

The research paper framing is **Evidence-Backed HPC Site Policies with Agentic Discovery**.

`login-measurements.json` is the single site input. It contains measured or simulated identity,
storage, platform, and scheduler facts. A **site profile** is the larger evidence-backed,
actionable output constructed from measurements, documentation, and eventually pilot jobs.

The working machine-readable site-profile contract, field semantics, evidence boundary, and a
full illustrative example are described in [docs/SITE_PROFILE.md](docs/SITE_PROFILE.md).
Common scheduler-independent login-node observations and their safety boundary are defined in
[docs/MEASUREMENT_FIELDS.md](docs/MEASUREMENT_FIELDS.md).
The versioned measurement result and its profile mappings are described in
[docs/LOGIN_MEASUREMENTS.md](docs/LOGIN_MEASUREMENTS.md).

The project combines four evidence sources:

1. existing site profiles;
2. login-node measurements;
3. official documentation analyzed with bounded AI assistance;
4. predefined pilot-job results.

One bounded discovery agent uses reviewed web-search and page-download tools, then AI selects among
the fetched sources and proposes structured facts. Measurements, pilot scripts, evidence
validation, reconciliation, and the final preflight decision remain deterministic.

## Project status

The deterministic foundation and replayable documentation pipeline are complete:

- an installable `src/` package;
- a working CLI and command hierarchy;
- run-level and step-level performance tracking;
- typed site, measurement, profile, and evidence contracts;
- simulated Anvil, Stampede3, and Notre Dame CRC inputs;
- measurement and documentation-backed partial profile construction;
- tests;
- explicit `NotImplementedError` messages for unfinished stages.

See [MILESTONES.md](MILESTONES.md) for the planned sequence of small implementation prompts.

## Main workflows

### Build a site profile

A backpack is not required.

```text
login measurements
→ profile lookup
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
login measurements
→ documentation discovery
→ corpus
→ context selection
→ extraction
→ evidence validation
```

The implemented context modes are `full-corpus`, `bm25`, and `llm-expanded-bm25`. See
[docs/DOCUMENTATION_WORKFLOW.md](docs/DOCUMENTATION_WORKFLOW.md) for the code and artifact flow.
For a repository-wide reading order, see [docs/CODE_GUIDE.md](docs/CODE_GUIDE.md). The evaluated
Anvil live-AI example is traced in [docs/RUN_RESULT.md](docs/RUN_RESULT.md).

## Site, model, and web modes

The three concerns are independent:

- `--site-mode simulate` is the default, requires `--measurements`, and never queries local
  hardware. `live` reuses `--measurements` when supplied or captures them from the login node.
- `--model-mode live` is the default and calls the model's inferred provider. `simulate` replays
  model responses.
- `--web-mode live` is the default and searches and fetches allowed official domains. `simulate`
  replays search results and pages.

The normal laptop workflow therefore simulates only the HPC site while using live web discovery
and live model calls. Offline tests explicitly simulate all three external inputs. Evidence retains
whether it was simulated or measured.

Documentation discovery also accepts optional user guidance. `--site-name` supplies a search name
and is required only for live collection when measurements are absent. `--discovery-note` adds
free-text context for the discovery model, and each repeatable `--discovery-keyword` adds a bounded
search query. There is no exclusion-keyword option: source scope and allowed domains remain
deterministic controls.

`--model` accepts a provider-neutral model identifier. The current registry maps `gpt-` and
OpenAI `o`-series names to OpenAI, `claude-` names to Anthropic, and `gemini-` names to Gemini.
Only the OpenAI adapter is implemented today; recognized future providers fail explicitly.

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

`evidence capture-login` writes the structured site input without running documentation or profile
construction. `profile build` constructs measurement and documentation-backed partial profiles.
`evaluate documentation` runs the documentation subsystem alone. Other unfinished commands create
run reports and fail explicitly.

Generated profile JSON follows the `SiteProfile` schema order instead of alphabetical key order.
The evidence-report reference and field-evidence links are the final top-level fields.

For a normal laptop run, set `OPENAI_API_KEY`, then run:

```bash
hpc-site-preflight profile build \
  --measurements examples/simulate/anvil/login-measurements.json \
  --model gpt-5-mini \
  --output-dir artifacts/anvil-live
```

Progress is written to standard error before slow searches, downloads, and model calls. When using
`conda run`, add `--no-capture-output` so Conda streams that output instead of holding it until the
process exits:

```bash
conda run --no-capture-output -n hpc-site-preflight hpc-site-preflight profile build --help
```

Add `--model-mode simulate --web-mode simulate` for a fully offline replay.

On a real login node, capture only the site input:

```bash
hpc-site-preflight evidence capture-login --short-site-name Anvil
```

Or let profile construction capture and retain it under the output directory:

```bash
hpc-site-preflight profile build \
  --site-mode live \
  --short-site-name Anvil \
  --model gpt-5-mini \
  --output-dir artifacts/anvil-live
```

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

The existing `hpc-site-policy-agent` repository is the reference implementation for documentation
discovery and extraction. Its useful components are adapted into the bounded documentation
pipeline; its top-level CLI and control loop are not copied into this project.

## Trust boundary

The project must not allow an LLM to:

- execute arbitrary shell commands;
- generate unrestricted network tests;
- submit unapproved pilot jobs;
- directly certify a site policy;
- approve or launch a real workflow;
- silently resolve conflicting evidence.

See [AGENTS.md](AGENTS.md) for authoritative implementation rules.
