# Documentation pipeline TODO

This file records the agreed incremental redesign of documentation discovery and extraction. Make
one change at a time, run the Anvil evaluation after each step, and do not continue automatically.

## Decisions to preserve

- Documentation discovery is the job of one bounded agent. Search and page download are the
  agent's reviewed tools; the model supplies source-selection judgment inside that agent.
- Use DuckDuckGo through the `ddgs` package for live search.
- Treat query construction, filtering, ranking, and fetch selection as deterministic given the
  returned search results. Record live results and pages for reproducible replay.
- Use AI to select useful fetched documents and extract typed policy values, not as the primary
  search engine.
- Keep `site`, `model`, and `web` modes independent.
- Keep domain allowlists, target/sibling scope checks, exact evidence spans, local validation, and
  deterministic profile construction.
- Keep all three context modes: `full-corpus`, `bm25`, and `schema-expanded-bm25`.
- Unknown fields remain empty and become explicit unresolved work; they do not fail the run.

## Step 1 — Replace the discovery loop

**Status:** Implementation complete; live Anvil comparison pending.

Replace the model-driven search/fetch loop with deterministic bootstrap discovery followed by one
bounded AI source-selection call.

- Generate fixed searches for canonical documentation, submission/resources, networking, and
  operational/storage policy; append user discovery keywords within the search budget.
- Execute searches with DuckDuckGo, enforce the HTTPS domain allowlist, classify target and sibling
  sites locally, rank candidates, fetch the canonical guide, follow useful guide links, and fetch a
  small number of high-ranking pages per topic.
- Give the model compact metadata and excerpts from already fetched pages. The model may only select
  fetched eligible pages and report unanswered topics.
- Permit at most one correction call for an invalid selection. Preserve deterministic crawl results
  as a partial selection if the model fails.
- Do not put complete downloaded pages into repeated model history.

Test after this step: run Anvil with live model and web modes and compare the new run with the saved
baseline in `docs/RUN_RESULT.md`.

Record at least:

- model calls, input/output tokens, and elapsed time;
- searches, successful and failed fetches, and selected pages;
- corpus document/chunk counts;
- topic coverage and unresolved documentation topics.

Target: reduce discovery from eight model calls to one normally, fetch multiple relevant Anvil
pages, cover every planned topic, and avoid repeated-search actions.

Offline Anvil replay now uses ten fixed searches, fetches two target-site pages, builds four corpus
chunks, and makes four model calls total: one source selection plus three extraction groups.

## Step 2 — Restore field-level retrieval

**Status:** Implementation complete; live context-mode comparison pending.

Retrieve evidence for individual profile fields instead of using only one broad query per extraction
group.

- Define multiple fixed query variants for each requested field.
- Apply site scope before scoring, deduplicate identical content, and add only small reviewed noise
  guards where evaluation demonstrates a need.
- Preserve the three context modes. `full-corpus` remains the no-ranking comparison;
  `bm25` uses basic field queries; `schema-expanded-bm25` adds reviewed field vocabulary.
- Group the selected field-local chunks only when constructing model requests.
- Persist retrieval queries, scores, selected chunk IDs, and retrieved-but-uncited chunks for audit.

Test after this step: verify that the Anvil partition-limit table is retrieved for partition and
walltime fields, then compare all three context modes.

Offline replay now retrieves the Anvil queue-limit table for `maximum_walltime_seconds` in all
three modes. `documentation-evidence.json` records field queries, fused scores, selected chunk IDs,
and whether each retrieved chunk was cited. The run still makes four model calls total.

## Step 3 — Use canonical typed extraction schemas

Replace the generic `field + resource + value` candidate list with small typed schemas whose names
and value shapes match the site-profile contract.

- Use independent submission/resource, networking, and operational/storage schemas.
- Represent submission options, partitions, limits, connectivity, charging, and purge values with
  canonical field names and types.
- Let the model select field-local evidence-span IDs; Python inserts exact quotes and provenance.
- Validate each field independently and retry only invalid fields once.
- Remove the fragile string-name translation that currently sits between accepted findings and
  profile fields.

Test after this step: confirm that accepted submission options and partition limits map directly to
profile fields and every applied value has evidence.

## Step 4 — Simplify after quality is stable

Simplify artifacts and module boundaries only after the first three steps produce stable output.

- Keep one compact actionable `site-profile.json` and one detailed `evidence-report.json` as the
  primary profile-build artifacts.
- Retain documentation-only evaluation data where needed for the paper, but avoid duplicating the
  same findings across multiple normal-run files.
- Keep the CLI parser, process lifecycle, and top-level operations separate.
- Review adapters and placeholder modules using the real call path; combine wrappers that add no
  safety, provider boundary, or test seam.
- Update `docs/CODE_GUIDE.md`, `docs/AI_PIPELINE.md`, figures, and run-result documentation after the
  final structure is known.

Test after this step: reproduce the Anvil result, validate all artifact links, and confirm that the
documented code-reading path matches the actual call graph.

## Deferred work

Do not mix these tasks into the documentation redesign:

- richer Slurm `sinfo` login measurements;
- partition CPU, memory, GPU, and node aggregation;
- live login-node collection;
- pilot jobs;
- backpack preflight;
- Anthropic and Gemini provider implementations.
