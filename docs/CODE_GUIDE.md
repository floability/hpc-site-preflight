# Code guide

This guide explains how to read the repository as a research system. Start with the data contracts,
follow one `profile build`, and only then read individual algorithms. The project is intentionally a
pipeline of small transformations rather than one general-purpose agent.

## The central research idea

Portable workflow packages describe what a workflow needs. This repository builds the other input:
a machine-readable account of how a particular HPC site must be used.

Keep four boundaries in mind while reading:

1. **Acquire evidence:** simulated or measured login facts, official documentation, and eventually
   approved pilots.
2. **Validate evidence:** check types, site scope, exact citations, and provenance.
3. **Construct policy:** deterministic code maps accepted evidence into a partial site profile.
4. **Preflight a workflow:** future deterministic code will compare workflow needs with the profile.

The model is allowed to select among bounded documentation results and propose typed facts. It does
not measure machines, run shell commands, decide evidence precedence, or write the final profile.

## Fastest reading path

For a first pass, read these files in order:

1. `docs/SITE_PROFILE.md` — understand the intended final product.
2. `examples/simulate/anvil/login-measurements.json` — see the single structured site input.
3. `src/hpc_site_preflight/measurements/base.py` — read its typed contract.
4. `src/hpc_site_preflight/measurements/capture.py` — see how live values are collected.
5. `src/hpc_site_preflight/cli.py` — see process lifecycle and operation dispatch.
6. `src/hpc_site_preflight/operations.py` — follow `build_profile()` and
   `_build_documentation()`.
7. `src/hpc_site_preflight/documentation/pipeline.py` — see the documentation stages in
   one short method.
8. `src/hpc_site_preflight/documentation/discovery_agent.py` — see the discovery agent use web
   tools and request one source selection.
9. `src/hpc_site_preflight/documentation/extraction.py` — see model proposals become validated
   findings.
10. `src/hpc_site_preflight/profiles/compiler.py` and `profiles/documentation.py` — see evidence
   become the final profile.
11. `docs/RUN_RESULT.md` — connect those functions to one actual run.

This path gives the complete implemented story without first reading provider plumbing or future
placeholders.

## One-command call graph

```text
__main__.py
  -> cli.main()
     -> cli_parser.build_parser()
     -> RunTracker(...)
     -> operation dispatch
     -> operations.build_profile()
        -> SimulatedMeasurementProvider.collect()
           or LiveMeasurementProvider.collect()
        -> compile_profile()
           -> build initial measurement-backed profile and evidence
        -> operations._build_documentation()
           -> provider_for_model()
           -> create_live_model_provider() or RecordedModelProvider
           -> LiveWebBackend or RecordedWebBackend
           -> DocumentationPipeline.build()
              -> build_site_identity()
              -> build_query_plan()
              -> DiscoveryAgent.run()
                 -> DocumentationTools.search_web()/fetch_page()
                 -> ModelProvider.generate_structured() for source selection
              -> build_corpus()
              -> write_corpus()
              -> extract_documentation()
                 -> select_context()
                 -> build exact spans
                 -> ModelProvider.generate_structured()
                 -> validate findings locally
        -> apply_documentation()
           -> apply accepted findings to reviewed profile fields
        -> write_json() for final artifacts
     -> RunTracker.finalize()
```

There are no hidden workflow-framework transitions in this chain.

## Data contracts first

The easiest way to understand each module is to know which typed object it receives and returns.

| Contract | Defined in | Role |
| --- | --- | --- |
| `MeasurementBundle` | `measurements/base.py` | Site identity, storage, platform, and scheduler observations |
| `SiteIdentity` | `documentation/models.py` | Normalized identity used for search and scope |
| `DiscoveryResult` | `documentation/models.py` | Pages selected by bounded discovery |
| `CorpusDocument`, `CorpusChunk` | `documentation/models.py` | Persistent normalized documentation |
| Three extraction result types | `documentation/models.py` | Schema-constrained model proposals |
| Typed documentation findings | `documentation/models.py` | Locally validated values ready for profile application |
| `DocumentationEvidence` | `documentation/models.py` | Accepted, rejected, and unresolved documentation output |
| `EvidenceRecord` | `evidence/models.py` | Detailed provenance for one value |
| `SiteProfile` | `profiles/models.py` | Compact machine-readable site policy |
| `EvidenceReport` | `evidence/bundle.py` | Detailed evidence linked from the profile |
| `RunPerformance` | `reporting/models.py` | Timing, model usage, tools, failures, and artifacts |

All external JSON is validated with Pydantic and rejects unknown keys. This is important to the
paper: an AI response and a valid policy field are different stages and different contracts.

## Repository map

### Entry point and shared behavior

- `__main__.py` forwards `python -m hpc_site_preflight` to the CLI.
- `cli_parser.py` defines commands and arguments. It performs no work.
- `cli.py` owns environment loading, one `RunTracker`, operation dispatch, error handling, and the
  final exit code.
- `operations.py` implements `profile build` and `evaluate documentation`, chooses site/model/web
  providers, and writes their artifacts.
- `config.py` holds small application configuration such as the run directory.
- `exceptions.py` defines explicit project errors. Unfinished public operations raise
  `FeatureNotImplementedError` instead of returning fake results.

Read `cli_parser.py` only when you need to understand user input. For execution, read `cli.main()`
and then the selected function in `operations.py`.

### Measurements

- `measurements/base.py` defines the structured external bundle and provider interface.
- `measurements/simulated.py` loads and validates a supplied bundle without reading hardware.
- `measurements/capture.py` uses reviewed local commands and Python APIs to collect the same shape.
- `measurements/live.py` validates newly captured values before the pipeline consumes them.

`--site-mode simulate` requires `--measurements`. In live mode, a supplied file is reused; otherwise
the collector runs and saves `login-measurements.json` under the profile output directory.

The JSON field catalogs under `schemas/measurement-fields/` describe what future collectors may
observe. They are allowlists/design contracts, not collected values.

The `0.6` bundle groups addressable site, storage, Slurm, and HTCondor values. The profile compiler
uses stored path patterns, populates login-visible facts, and leaves compute-node behavior for
documentation or pilots. See `docs/LOGIN_MEASUREMENTS.md`.

### Documentation models

- `documentation/models.py` contains the shared contracts for identity, queries, source selection,
  pages, corpus records, extraction proposals, citations, and final documentation evidence.

Read `documentation/models.py` by following this type sequence:

```text
SiteIdentity -> QueryPlan -> DiscoverySelection -> DiscoveryResult
             -> CorpusChunk -> group extraction result -> typed findings
             -> DocumentationEvidence
```

The three model result types are `SubmissionExtractionResult`, `NetworkExtractionResult`, and
`OperationalExtractionResult`. Start with those classes, then follow `_validate_group()` in
`documentation/extraction.py`. Accepted values become specific classes such as
`SubmissionOptionFinding` and `PartitionFinding`; `profiles/documentation.py` applies those types
directly without interpreting a generic field-name string.

### Model providers

- `providers/base.py` defines a single operation: return a locally validated Pydantic result for a
  structured request. Provider usage is written directly to the run tracker.
- `providers/openai.py` calls the OpenAI Responses API and reports provider token usage to the
  tracker.
- `providers/openai_schema.py` converts Pydantic schemas into the strict form accepted by the API.
- `providers/recorded.py` replays ordered local responses for offline tests.
- `providers/registry.py` maps a model name to a provider and creates the implemented live adapter.

The registry recognizes future Anthropic and Gemini model prefixes, but their adapters intentionally
raise an explicit not-implemented error today.

### Site identity and scope

- `documentation/identity.py` reads identity, hostname, scheduler, and domain observations, creates
  deterministic topic queries, and classifies documentation scope.

The optional `--site-name` can guide discovery; it is required when a live run must capture missing
measurements. `--discovery-note` is included in the typed identity shown to the discovery model.
Each deduplicated `--discovery-keyword` adds a separate search query. Allowed domains and
deterministic source scope remain the primary exclusion controls.

This module owns a major trust decision. Target-site documents may support policy; sibling-site and
organization-general documents may help discovery but cannot become target policy.

### Web acquisition

- `documentation/tools.py` defines the backend interface, a live backend, a recorded backend, and the
  bounded `DocumentationTools` exposed to discovery.

The tools enforce HTTPS, allowed domains, query/page budgets, timeout, page size, fetch-before-select,
and target-site selection. The model never receives a general browser or network client.
Browser-only URL fragments are removed at this boundary so anchors cannot consume additional fetch
slots or appear as duplicate source-selection candidates.

### The implemented agent

- `documentation/discovery_agent.py` contains `DiscoveryAgent`, the only implemented agent.

The agent owns two reviewed tools: documentation search and page download. It executes fixed topic
queries, filters and ranks results locally, downloads a bounded candidate set, and follows allowed
links. It then asks the model for a typed `DiscoverySelection`. The response may only name already
fetched target-site URLs, and it may request another bounded search step using at most three search
queries. The same domain and scope checks apply to those queries and newly fetched pages. Discovery
defaults to two steps; `--max-discovery-steps 1` performs one selection call. One correction is
allowed for an invalid selection across the run, and a model failure preserves the fetched pages.

The top-level `agent/` package is different. It defines future evidence-controller actions and
state, but `agent/controller.py` is not implemented or called by `profile build`. That later
controller will choose among approved high-level evidence actions; it will not replace the bounded
documentation agent.

### Corpus and retrieval

- `documentation/corpus.py` normalizes HTML into stable documents and heading-aware chunks, keeps
  tables intact, hashes content, and writes JSONL artifacts.
- `documentation/retrieval.py` implements `full-corpus`, `bm25`, and
  `llm-expanded-bm25` context selection.
- `documentation/query_expansion.py` makes the single bounded typed query-expansion call used only
  by `llm-expanded-bm25`.

BM25 scoring is deterministic. It filters scope and duplicate content first, then ranks each
requested field. Normal BM25 uses reviewed queries. LLM-expanded BM25 retains those queries and
adds bounded model-generated synonyms and site-specific variants, with larger but explicit hit and
group limits. Full-corpus covers every eligible chunk once in stable, 12,000-character batches and
uses one combined typed extraction per batch. Ranked field results are merged for the three group
requests. The model sees local corpus chunks, not an unrestricted remote page.

### Extraction and validation

- `documentation/extraction.py` builds exact local spans, sends structured group requests, validates
  proposals, optionally asks for one correction, and returns `DocumentationEvidence`.

This is the most important file for the AI/evidence boundary. Read it in this order:

1. the field groups and expected value/resource rules;
2. `extract_documentation()`;
3. the per-group extraction function;
4. span construction;
5. local finding validation;
6. unresolved-field construction.

The model returns span IDs. Exact quotes are copied by Python from the span library, so the model
cannot rewrite a citation and have it accepted as exact evidence.

### Linear documentation orchestration

- `documentation/pipeline.py` contains `DocumentationPipeline`, the short composition root for
  documentation discovery, corpus construction, and extraction.

Its `build()` method is the best single file for seeing the AI workflow. It creates identity and
queries, runs discovery, builds the corpus, then calls extraction. It returns documentation evidence;
it does not build the site profile.

### Evidence

- `evidence/models.py` defines evidence records, field links, conflicts, and unresolved actions.
- `evidence/bundle.py` defines the detailed `EvidenceReport`.
- `evidence/provenance.py` creates stable evidence IDs.
- `evidence/reconciliation.py` contains the reviewed field/source rules currently used when applying
  documentation.

This package is where the project should eventually make precedence and conflict decisions. Those
decisions remain deterministic.

### Profile construction

- `profiles/models.py` defines the compact, actionable `SiteProfile` and its shallow resource arrays.
- `profiles/compiler.py` converts measurement paths into scheduler, partition, resource, storage,
  accounting, and software profile fields. It also creates measurement evidence and unresolved
  work.
- `profiles/documentation.py` contains the small allowlisted mapping from validated documentation
  fields to exact profile paths and appends documentation provenance.
- `profiles/repository.py` and `profiles/freshness.py` are future placeholders for profile lookup and
  field freshness.

The construction order matters:

```text
measurements -> base partial profile + measurement evidence
documentation findings -> reviewed field mapping + documentation evidence
remaining nulls -> explicit unresolved actions
```

The model never receives a mutable `SiteProfile`.

### Reporting

- `reporting/models.py` defines stage and run metrics.
- `reporting/tracker.py` owns one `RunTracker`, sequential stage contexts, aggregation, and final
  persistence.
- `reporting/trace.py` writes safe JSONL events.
- `reporting/console.py` formats live stage/progress/run summaries.
- `reporting/artifacts.py` writes JSON artifacts while preserving schema field order. In a site
  profile, evidence references remain last.

Every model adapter and documentation tool receives the same tracker. Provider-reported token counts
are recorded; missing counts remain unavailable rather than being estimated.

The tracker prints concise progress to standard error before potentially slow external calls.
Repeated internal tool stages remain in the performance report and trace without producing verbose
stage summaries in the terminal.

### Future pipeline packages

These contracts exist, but their operational functions deliberately fail as unfinished:

- `probes/`: approved simulated and live pilot jobs for compute-node-only facts;
- `backpack/`: normalized portable-workflow requirements;
- `preflight/`: deterministic compatibility checks, remediation, and execution plans;
- `agent/`: the future high-level evidence-action controller;
- profile lookup/freshness modules.

Do not include these placeholders when describing the current experimental result as implemented.
They document intended boundaries and make accidental fake behavior difficult.

## Artifact ownership

| Artifact | Producer | Consumer or purpose |
| --- | --- | --- |
| `corpus/manifest.json` | `documentation/corpus.py` | Corpus identity and reproducibility |
| `corpus/documents.jsonl` | `documentation/corpus.py` | Normalized fetched pages |
| `corpus/chunks.jsonl` | `documentation/corpus.py` | Retrieval and citation audit |
| `documentation-evidence.json` | `documentation/extraction.py`, written by CLI | Findings, field-level retrieval scores, and citation use |
| `site-profile.json` | `profiles/compiler.py` plus `profiles/documentation.py` | Future deterministic preflight input |
| `evidence-report.json` | profile compiler and documentation mapper | Full provenance behind compact profile fields |
| `performance.json` | `reporting/tracker.py` | Timing, requests, tokens, tools, failures, artifacts |
| `trace.jsonl` | `reporting/trace.py` through tracker | Ordered code/tool/model progress trace |

## How to inspect a run

Use the artifacts in this order:

1. `performance.json`: did the command finish, how expensive was it, and which stage failed?
2. `trace.jsonl`: what action happened in what order?
3. `documentation-evidence.json`: what did the model propose, what was rejected, and what remains
   unresolved?
4. `corpus/chunks.jsonl`: does each cited or retrieved chunk actually contain relevant text?
5. `site-profile.json`: what compact values would downstream code consume?
6. `evidence-report.json`: which evidence record supports each populated profile field?

Never assess a run from the console's `completed` line alone. Check `profile_state`, unresolved
work, rejected findings, evidence links, and any internally failed tracker stages.

## Tests as executable explanations

- `test_models.py` covers core external contracts.
- measurement catalog tests cover reviewed common, Slurm, and HTCondor fact definitions.
- `test_simulated_measurement_provider.py` explains input validation and site matching.
- `test_openai_provider.py` and `test_provider_registry.py` explain model/provider behavior.
- `test_phase_c.py` follows measurement-only profile construction.
- `test_phase_d.py` is the broadest executable description of documentation identity, bounded tools,
  discovery, corpus, retrieval, extraction, mapping, and CLI integration.
- `test_tracker.py` explains stage, usage, failure, and artifact reporting.
- `test_cli.py` covers command parsing, dispatch, discovery hints, JSON field order, explicit
  unfinished behavior, and exit codes.

For a focused paper review, read each production module beside its corresponding test. The tests
often show the intended trust boundary more directly than comments do.

## Mapping the code to the paper claims

| Paper claim | Current code |
| --- | --- |
| Login facts are cheap and observable | measurement catalogs and `MeasurementBundle` |
| Documentation contains non-measurable policy | documentation identity, discovery, corpus, retrieval, extraction |
| AI is bounded | typed discovery decisions, allowed domains, budgets, structured extraction |
| Every accepted policy value has evidence | exact spans, `EvidenceRecord`, field evidence links |
| Unknowns do not abort construction | partial profiles and unresolved work items |
| Scheduler display is not enforced policy | separate visible and maximum walltime fields |
| Compute-node facts require costly pilots | unresolved `run_pilot` actions; implementation is future work |
| Final compatibility decisions are deterministic | profile compiler exists; workflow preflight is future work |

## Current implementation boundary

Implemented end to end:

- supplied simulated or measured login measurements;
- bounded live login capture with basic Slurm facts and HTCondor detection;
- live or recorded model calls;
- live or recorded documentation search/fetch;
- bounded discovery;
- corpus construction;
- three retrieval modes;
- structured extraction and exact evidence validation;
- deterministic partial profile construction;
- run tracing and performance reporting.

Not yet implemented end to end:

- detailed HTCondor pool collection and additional login facts;
- simulated or approved live pilots;
- complete evidence reconciliation and conflict handling;
- backpack loading and deterministic workflow preflight;
- the high-level evidence controller;
- the paper evaluation harness.

That boundary should be stated explicitly in the paper until the later milestones are complete.
