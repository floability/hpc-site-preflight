# Documentation workflow

This document follows the Phase D code in execution order. Each module has one primary job.

## File reading order

Read the small contracts first, then follow the transformations, and finish with orchestration:

1. `site_descriptor/models.py` and `measurements/base.py` define the two starting inputs.
2. `documentation/models.py` defines every value passed through the documentation pipeline.
3. `providers/base.py` defines one structured-model operation; `providers/recorded.py` and
   `providers/openai.py` implement it.
4. `documentation/identity.py` normalizes site identity, creates fixed topic queries, and classifies
   scope.
5. `documentation/tools.py` applies all search and page-fetch bounds around a replaceable backend.
6. `documentation/discovery_agent.py` runs one agent that uses bounded search/download tools, then
   asks the model to select sources once.
7. `documentation/corpus.py` converts selected pages into stable documents and chunks.
8. `documentation/retrieval.py` implements full corpus, BM25, and expanded BM25 selection.
9. `documentation/extraction.py` creates exact spans and validates the model's typed proposals.
10. `documentation/pipeline.py` calls those documentation stages in a straight line.
11. `profiles/documentation.py` maps accepted findings onto known profile fields.
12. `profiles/compiler.py` builds the measurement profile and applies documentation findings.
13. `operations.py` loads files, chooses providers, calls the pipeline, and writes artifacts.
14. `cli_parser.py` defines arguments; `cli.py` dispatches the selected operation and owns the run
    lifecycle.

The simulated model provider reads `documentation-model.json`; the live provider sends the same
typed requests to the OpenAI Responses API. The independent web mode either searches and fetches
official allowed domains or replays `documentation-web.json`.

Optional `--site-name`, `--discovery-note`, and repeatable `--discovery-keyword` arguments guide
discovery without changing the canonical site record or allowed domains.

## What happens during `profile build`

```text
site-descriptor.json + login-measurements.json
  |
  |-- validate both files and confirm site/scheduler agreement
  |
  |-- build deterministic site identity
  |     name, aliases, scheduler, host signals, allowed domains, path tokens
  |
  |-- create fixed topic search queries
  |     canonical guide, submission, resources, storage, networking
  |
  |-- run one bounded discovery agent
  |     deterministic search, ranking, download, and guide-link following
  |     browser-only URL fragments are removed before ranking and fetch
  |     one model call selects from fetched target-site pages
  |
  |-- build corpus/
  |     manifest.json, documents.jsonl, chunks.jsonl
  |     headings and tables are preserved
  |
  |-- retrieve context independently for each requested field
  |     mode = full-corpus | bm25 | schema-expanded-bm25
  |     merge field-local chunks into submission, network, operational requests
  |
  |-- create exact sentence and table-row span IDs
  |
  |-- request structured findings
  |     model returns typed group values and span IDs
  |
  |-- validate locally
  |     reject unknown fields, resources, spans, scopes, and value types
  |     retry invalid findings once, then leave them unresolved
  |
  |-- compile measurements into the base partial profile
  |
  |-- apply accepted documentation findings deterministically
  |     append exact quotes and URLs to evidence-report.json
  |
  `-- write site-profile.json and all supporting artifacts
```

The model never supplies the final quote, decides source scope, changes precedence rules, or writes
the profile. It selects among fetched sources and proposes typed findings that deterministic code
may accept or discard.

## Simulated site and replay input files

Each directory under `examples/simulate/` contains:

- `site-descriptor.json`: explicit site identity and documentation scope;
- `login-measurements.json`: simulated normalized measurements;
- `documentation-web.json`: optional simulated search results and normalized pages; and
- `documentation-model.json`: optional simulated source selection and extraction results.

The first two files simulate the HPC site. The documentation files are used only with simulated
web or model mode. Model recordings omit token counts because they are not provider-reported usage.

## Outputs

`profile build` writes:

- `site-profile.json`: compact actionable partial profile;
- `evidence-report.json`: measurement and documentation provenance;
- `documentation-evidence.json`: findings plus field queries, retrieval scores, and citation use;
- `corpus/manifest.json`;
- `corpus/documents.jsonl`;
- `corpus/chunks.jsonl`; and
- run-level `performance.json` and `trace.jsonl` files.

Normal traces include tool names, URLs, content hashes, and counts. Full page bodies stay in the
corpus and are not copied into the trace.

The terminal prints short progress messages before searches, downloads, and model calls. Detailed
queries, results, hashes, and timing remain in `trace.jsonl` and `performance.json`. Use
`conda run --no-capture-output` when launching through Conda; ordinary `conda run` captures the
child process output and can make a live run appear stuck.

## Commands

```bash
hpc-site-preflight profile build \
  --site-descriptor examples/simulate/anvil/site-descriptor.json \
  --measurements examples/simulate/anvil/login-measurements.json \
  --model-mode simulate \
  --web-mode simulate \
  --context-mode bm25 \
  --output-dir artifacts/anvil

hpc-site-preflight evaluate documentation \
  --site-descriptor examples/simulate/anvil/site-descriptor.json \
  --measurements examples/simulate/anvil/login-measurements.json \
  --model-mode simulate \
  --web-mode simulate \
  --context-mode schema-expanded-bm25 \
  --output-dir artifacts/anvil-documentation
```
