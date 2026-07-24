# Improvement TODO

This file records improvements discovered through live Anvil runs. Implement one item at a time,
rerun its focused test, and do not advance automatically.

## Decisions to preserve

- One bounded discovery agent uses deterministic DuckDuckGo search and reviewed download tools.
- AI selects documentation, expands queries, and proposes typed findings; validation and profile
  construction remain deterministic.
- Every accepted value requires target-site evidence, and documentation silence remains unknown.
- Keep `full-corpus`, `bm25`, and `llm-expanded-bm25` as separate evaluation modes.

## Latest Anvil checkpoint

The v3 runs completed with live web and model modes:

| Mode | Selected chunks | Model calls | Tokens | Time |
| --- | ---: | ---: | ---: | ---: |
| Batched full corpus | 310 | 16 | 91,333 | 350.0 s |
| BM25 | 22 | 6 | 29,346 | 155.7 s |
| LLM-expanded BM25 | 34 | 6 | 31,971 | 182.1 s |

All modes found the allocation requirement and eight documented partition walltimes. The runs also
exposed the following correctness and evaluation problems.

## 1. Enforce abstention

**Priority: Highest**

- Reject network findings that interpret missing documentation as `false`.
- Do not treat MPI or multi-node execution as proof of arbitrary TCP connectivity.
- Leave undocumented compute networking `null` and assign it to documentation follow-up or pilots.
- Add tests for explicit permission, explicit prohibition, indirect evidence, and silence.

**Test:** Anvil documentation must leave all three compute-network fields unknown with the current
corpus.

## 2. Constrain canonical names

**Priority: High**

- Constrain Slurm and HTCondor option names in the typed schema instead of accepting free text.
- Give extraction the allowed canonical option, partition, and storage names.
- Normalize documented forms such as `-A`, `--account`, and `$SCRATCH` before validation.
- Ensure correction calls return only invalid fields and can repair names.

**Test:** Anvil must populate required `account` and `partition` options without accepting unknown
options or storage resources.

## 3. Make expanded BM25 truly additive

**Priority: High**

- Preserve ordinary BM25 hits before adding hits from model-expanded queries.
- Append new deduplicated hits within the expanded budget instead of globally reranking base hits.
- Retain the Anvil partition requirement chunk `doc-anvil-jobs:c44`.

**Test:** Every base BM25 hit must remain in the expanded result, with at least one additional hit
when expansion finds new evidence.

## 4. Improve discovery coverage

**Priority: High**

- Balance selected pages across submission, resources, filesystem storage, networking, and policy.
- Distinguish filesystem storage documentation from unrelated object-storage documentation.
- Preserve partial results when a fetch fails, but keep missing topics visible.

**Test:** Anvil discovery must include the official filesystem page that states scratch and project
retention rules.

## 5. Freeze the corpus for retrieval evaluation

**Priority: High**

- Add an extraction path that loads a previously captured corpus without rerunning discovery.
- Run all retrieval modes against the same documents, chunks, model, and field schemas.
- Repeat each mode to measure correctness, cost, latency, and model variance separately from
  discovery variance.

**Test:** One command must produce comparable full-corpus, BM25, and expanded-BM25 results from an
identical corpus fingerprint.

## 6. Complete residual evidence and preflight

**Priority: Later**

- Add approved simulated and live pilots for unresolved compute networking and storage behavior.
- Reconcile measurement, documentation, and pilot evidence without hiding conflicts.
- Build deterministic preflight cases and measure false acceptance and false rejection.

**Test:** Reproduce RQ3 fault-injection results from versioned profiles and workflow requirements.

## Deferred engineering

- Finish HTCondor live collection and richer scheduler measurements.
- Mark runs with failed extraction groups as partial rather than fully completed.
- Consolidate normal-run artifacts after output quality stabilizes.
- Add Anthropic and Gemini provider adapters after the OpenAI path is stable.
