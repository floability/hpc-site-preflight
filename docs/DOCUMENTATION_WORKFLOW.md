# Documentation workflow

This document follows the Phase D code in execution order. Each module has one primary job.

## File reading order

Read the small contracts first, then follow the transformations, and finish with orchestration:

1. `site_info/models.py` and `measurements/base.py` define the two starting inputs.
2. `documentation/models.py` defines every value passed through the documentation pipeline.
3. `providers/base.py` defines one structured-model operation; `providers/recorded.py` and
   `providers/openai.py` implement it.
4. `documentation/identity.py` normalizes site identity, creates four queries, and classifies scope.
5. `documentation/web.py` applies all search and page-fetch bounds around a replaceable backend.
6. `documentation/discovery.py` runs the short model-directed search/fetch/finish loop.
7. `documentation/corpus.py` converts selected pages into stable documents and chunks.
8. `documentation/retrieval.py` implements full corpus, BM25, and expanded BM25 selection.
9. `documentation/extraction.py` creates exact spans and validates the model's typed proposals.
10. `documentation/policy_agent_adapter.py` calls those documentation stages in a straight line.
11. `profiles/documentation.py` maps accepted findings onto known profile fields.
12. `profiles/compiler.py` builds the measurement profile and applies documentation findings.
13. `cli.py` loads files, chooses providers, calls the pipeline, and writes artifacts.

The simulated model provider reads `documentation-model.json`; the live provider sends the same
typed requests to the OpenAI Responses API. The independent web mode either searches and fetches
official allowed domains or replays `documentation-web.json`.

## What happens during `profile build`

```text
site-info.json + login-measurements.json
  |
  |-- validate both files and confirm site/scheduler agreement
  |
  |-- build deterministic site identity
  |     name, aliases, scheduler, host signals, allowed domains, path tokens
  |
  |-- create four fixed search queries
  |     submission, resources, storage, networking
  |
  |-- run bounded discovery
  |     model chooses search_web, fetch_page, or finish_discovery
  |     application validates every action, URL, scope, and budget
  |
  |-- build corpus/
  |     manifest.json, documents.jsonl, chunks.jsonl
  |     headings and tables are preserved
  |
  |-- select context independently for three extraction groups
  |     submission, network, operational
  |     mode = full-corpus | bm25 | schema-expanded-bm25
  |
  |-- create exact sentence and table-row span IDs
  |
  |-- request structured findings
  |     model returns field, resource, value, and span IDs
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
the profile. It chooses discovery actions and proposes typed findings that deterministic code may
accept or discard.

## Simulated site and replay input files

Each directory under `examples/simulate/` contains:

- `site-info.json`: explicit site identity and documentation scope;
- `login-measurements.json`: simulated normalized measurements;
- `documentation-web.json`: optional simulated search results and normalized pages; and
- `documentation-model.json`: optional simulated discovery actions and extraction results.

The first two files simulate the HPC site. The documentation files are used only with simulated
web or model mode. Model recordings omit token counts because they are not provider-reported usage.

## Outputs

`profile build` writes:

- `site-profile.json`: compact actionable partial profile;
- `evidence-report.json`: measurement and documentation provenance;
- `documentation-evidence.json`: accepted, rejected, and unresolved documentation findings;
- `corpus/manifest.json`;
- `corpus/documents.jsonl`;
- `corpus/chunks.jsonl`; and
- run-level `performance.json` and `trace.jsonl` files.

Normal traces include tool names, URLs, content hashes, and counts. Full page bodies stay in the
corpus and are not copied into the trace.

## Commands

```bash
hpc-site-preflight profile build \
  --site-info examples/simulate/anvil/site-info.json \
  --measurements examples/simulate/anvil/login-measurements.json \
  --model-mode simulate \
  --web-mode simulate \
  --context-mode bm25 \
  --output-dir artifacts/anvil

hpc-site-preflight evaluate documentation \
  --site-info examples/simulate/anvil/site-info.json \
  --measurements examples/simulate/anvil/login-measurements.json \
  --model-mode simulate \
  --web-mode simulate \
  --context-mode schema-expanded-bm25 \
  --output-dir artifacts/anvil-documentation
```
