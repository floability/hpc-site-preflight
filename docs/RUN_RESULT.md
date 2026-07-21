# Run result: Anvil with live model and web

This document evaluates one representative profile build and traces each output back to the code
that produced it. It is a snapshot of the current implementation, not a claim that the generated
site profile is complete or correct ground truth.

## Run identity

```text
Run ID:       a7c92f79-9bac-4903-bc3d-87faefdf46bc
Command:      profile build
Site mode:    simulate
Model mode:   live
Web mode:     live
Model:        gpt-5-mini through OpenAI
Context mode: bm25
Status:       completed
Duration:     135.28 seconds
```

The command used simulated Anvil site inputs while performing real documentation search, page
fetching, and model calls. This is the intended laptop experiment: the HPC site is simulated, but
the AI and web are not.

The exact run records are:

- `runs/a7c92f79-9bac-4903-bc3d-87faefdf46bc/performance.json`
- `runs/a7c92f79-9bac-4903-bc3d-87faefdf46bc/trace.jsonl`
- `artifacts/anvil-live/`

## Overall judgment

The run was a **technical success and an incomplete policy result**.

- The complete implemented pipeline ran and wrote valid artifacts.
- Live provider token usage was reported: 12 requests, 84,267 input tokens, 8,512 output tokens,
  and 92,779 total tokens.
- The system preserved simulated measurements separately from official documentation evidence.
- Invalid documentation proposals were rejected locally, and unknown fields remained unresolved.
- Only one of two accepted documentation findings changed the profile.
- Important Anvil policy, especially enforced partition walltime, was not recovered even though a
  relevant queue-limit table exists in the downloaded page.

`status: completed` means the command finished without a top-level exception. It does **not** mean
the site policy is complete. The semantic result is `profile_state: partial`, with eight unresolved
work items.

## Inputs

| Input | Meaning | Important contents |
| --- | --- | --- |
| `examples/simulate/anvil/site-info.json` | Explicit target identity and documentation boundary | Purdue Anvil, Slurm, `purdue.edu`, Anvil hostname pattern |
| `examples/simulate/anvil/login-measurements.json` | Facts that stand in for a real login-node collection | 21 observations, three partitions, two node shapes, two storage paths |
| Live web backend | Official-documentation acquisition | HTTPS search and fetch limited to the configured domain |
| Live OpenAI provider | Structured discovery and extraction decisions | `gpt-5-mini` |

The simulated scheduler observations deliberately include `infinite` for the visible `shared` and
`wholenode` walltime. The compiler correctly normalizes that display value to `null` rather than
treating it as an enforced policy limit.

## Execution and code trace

### 1. CLI setup

`hpc_site_preflight.cli.main()` parsed the command, loaded environment configuration, and created
one `RunTracker`. At the time of this recorded run, `_profile_build_handler()` controlled the
linear build. The same behavior now lives in `operations.build_profile()` after the CLI organization
was simplified.

Produced:

- a run directory;
- an initial `performance.json`;
- an append-only `trace.jsonl`.

The tracker refreshed the performance report after every stage and finalized it even though one
internal fetch stage failed.

### 2. Site-information loading

Stage: `site_info_load`

`site_info.loader.load_site_info()` read the JSON and validated it as `SiteInfo`.

Produced in memory:

- canonical site ID and name;
- scheduler type;
- aliases and hostname patterns;
- allowed documentation domains and preferred URL tokens.

No standalone output file was produced because the supplied site-information file is already the
persisted input.

### 3. Simulated login measurements

Stages: `simulated_measurement_load`, `simulated_measurement_validate`

`SimulatedMeasurementProvider.collect()` read the measurement file, validated every observation,
and confirmed that its site ID, scheduler, and `evidence_source: simulated` matched the site.

Produced in memory: one normalized `MeasurementBundle` used by both documentation identity and
profile compilation.

This stage did not inspect the laptop hardware.

### 4. Documentation identity and query plan

Stage: `documentation_identity`

`PolicyAgentAdapter.build()` called `build_site_identity()` and `build_query_plan()`. These
functions combined explicit site information with measured host and scheduler signals, then made
four bounded policy-search queries.

Produced in memory:

- a normalized Anvil identity;
- fixed queries for submission, resources, storage/accounting, and networking.

The query plan was not persisted in this run, so it can currently be inspected only through code
or model/tool trace events.

### 5. Bounded discovery agent

Stages: eight `structured_model_call` stages interleaved with eight attempted
`documentation_tool` stages

`DiscoveryAgent.run()` asked the model for one typed action per turn. `DocumentationTools` checked
each action and `LiveWebBackend` performed the allowed search or fetch.

Observed action sequence:

1. Search for Anvil submission/account/partition documentation.
2. Fetch `https://docs.rcac.purdue.edu/userguides/anvil/jobs/`.
3. Search for queue, walltime, node, and job limits.
4. Repeat the same queue-limit search.
5. Attempt a page fetch that returned HTTP 404.
6. Search for scratch, purge, charging, and allocation policy.
7. Repeat the same storage-policy search.
8. Finish with the one successfully fetched target-site page.

Produced in memory: one selected `target_site` page.

The failed fetch did not abort discovery. That is desirable partial-output behavior. However, the
failed URL was not recorded in the trace, and the failed call was not included in the aggregate
tool-call count. The run reports seven successful tool calls and one failed tool stage.

The agent consumed its eight-turn budget before attempting the networking query. Repeated searches
therefore reduced policy coverage.

### 6. Corpus construction

Stage: `documentation_corpus`

`build_corpus()` normalized the selected page into one document and 154 heading-aware chunks.
`write_corpus()` persisted stable records.

Produced:

- `artifacts/anvil-live/corpus/manifest.json`;
- `artifacts/anvil-live/corpus/documents.jsonl`;
- `artifacts/anvil-live/corpus/chunks.jsonl`.

The corpus contains 124 text chunks and 30 table chunks. It preserves the useful queue-limit table,
but also contains small navigation-like chunks and line-number noise from code examples.

### 7. Context selection and exact spans

Stage repeated per group: `documentation_context_selection`

`extract_documentation()` processed three independent groups: `submission`, `network`, and
`operational`. `select_context()` used BM25 to choose at most eight chunks for each group, then the
extractor split those chunks into exact sentence or table-row spans.

Produced in memory:

| Group | Chunks | Spans | Notable result |
| --- | ---: | ---: | --- |
| submission | 8 | 20 | Found account/partition examples but missed the queue-limit table |
| network | 8 | 24 | Included the queue-limit table even though it was not network evidence |
| operational | 8 | 9 | Included the filesystem accounting statement |

The misplaced queue table explains an important result: BM25 had the needed walltime text in the
corpus but did not give it to the submission extractor.

### 8. Structured extraction and local validation

Stages: one `structured_model_call` and one `documentation_evidence_validation` per group, plus one
correction call for the operational group

The provider returned typed `ExtractionResult` objects. Local validation checked field names,
value types, resource names, source scope, and exact span IDs.

Produced:

- submission: one accepted finding;
- network: no findings;
- operational: three initial proposals rejected, then one corrected finding accepted;
- six documentation fields left unresolved.

The accepted findings were:

1. `required_submission_options` with human-readable account and partition labels;
2. `charging_model` with the exact quote “Filesystem storage is not charged.”

The operational correction demonstrates the intended trust boundary: three findings carrying an
invalid resource name were rejected by deterministic validation before the corrected result was
accepted.

### 9. Measurement profile compilation

Stage: `measurement_profile_build`

`compile_profile()` first converted observations into a base `SiteProfile` and an
`EvidenceReport`. It then called `apply_documentation()` to map only reviewed documentation fields
onto profile paths.

Produced in memory:

- a partial Anvil profile;
- 21 measurement evidence records;
- one applied documentation evidence record;
- field-to-evidence links;
- unresolved work items.

The `required_submission_options` finding did not change the profile because its values were
display labels such as `Account (-A or --account)`, while the deterministic mapper accepts canonical
option names such as `account`. No evidence records were created for that finding.

The filesystem sentence did map to `/accounting/charging_model`, creating one official
documentation evidence record and removing that field from the unresolved list.

### 10. Artifact writing

Stage: `profile_artifact_write`

The CLI serialized the three final contracts.

Produced:

- `artifacts/anvil-live/site-profile.json`;
- `artifacts/anvil-live/evidence-report.json`;
- `artifacts/anvil-live/documentation-evidence.json`.

The tracker registered all six profile/corpus artifacts and finalized the two run artifacts.

## Result contents

### Site profile

Useful populated fields include:

- Slurm and `sbatch`;
- Slurm client version `24.05.2`;
- visible partitions `shared`, `wholenode`, and `gpu`;
- a visible GPU walltime of 172,800 seconds;
- CPU and GPU resource shapes;
- home and scratch paths;
- visible account `mock-account`;
- the documented filesystem charging sentence.

Still unknown:

- enforced maximum walltime for all three partitions;
- manager-worker, worker-worker, and outbound compute networking;
- compute-node visibility of home and scratch storage.

The final profile has eight unresolved work items: three request more documentation and five request
approved pilots.

### Evidence report

```text
Evidence records:       22
  measurement:          21
  documentation:         1
Field evidence links:   17
Unresolved actions:      8
Conflicts:               0
```

The single documentation record retains the official URL, heading, chunk ID, exact quote, field
path, and value. Simulated measurement evidence is marked `illustrative`; live documentation is
marked `official`.

### Documentation evidence

The intermediate result retains both accepted findings, all three rejection reasons, six unresolved
field names, and the union of 23 selected chunk IDs. This separation is valuable because it shows
that an accepted AI proposal and a profile-applied policy value are not the same thing.

## What worked for the paper objective

- The run demonstrates cost-ordered evidence acquisition without requiring access to Anvil.
- Scheduler observations were not mistaken for enforced walltime policy.
- Official documentation was scoped, normalized, cited, and preserved locally.
- The model proposed findings, but deterministic code controlled validation and profile mutation.
- Missing policy produced a partial actionable work order instead of an exception.
- Provider-reported latency and token usage are available for evaluation.

## What needs improvement

1. **Discovery efficiency:** repeated searches consumed turns and caused the networking query to be
   skipped. Duplicate-action detection or deterministic query coverage is needed.
2. **Prompt cost:** discovery used 77,800 of 84,267 input tokens, about 92%. The full fetched page is
   repeatedly carried in discovery history. The history needs a compact page summary/reference.
3. **Retrieval quality:** BM25 did not route the known queue-limit table to the submission group.
   The three context modes should be compared before changing extraction logic.
4. **Canonical values:** `required_submission_options` should return canonical option identifiers,
   or deterministic normalization should translate display labels before mapping.
5. **Submission semantics:** the compiler currently marks every built-in Slurm option as required,
   which masks the failed documentation mapping. Supported syntax and site-required options need
   distinct semantics.
6. **Accounting semantics:** “filesystem storage is not charged” is narrower than a site-wide
   charging model. A resource-specific accounting representation would avoid overstating the
   result and the `accounting: documented` validation state.
7. **Trace completeness:** failed fetches should retain the attempted URL, and correction acceptance
   should receive an explicit progress event.
8. **Corpus quality:** navigation fragments, duplicate chunks, and rendered code line numbers should
   be reduced before retrieval evaluation.

## Recommended next experiment

Run the same inputs, provider, and live web backend with `schema-expanded-bm25`, then compare:

- recovered partition walltimes;
- selected chunks per extraction group;
- accepted and applied findings;
- input-token usage;
- unresolved profile fields.

That comparison will distinguish retrieval failure from extraction-schema failure before the code
is changed.
