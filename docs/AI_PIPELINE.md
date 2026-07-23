# AI pipeline

The AI pipeline finds official site documentation and proposes typed policy facts. It does not
measure the system, decide which evidence wins, or write the site profile directly.

## Inputs

The pipeline starts with three inputs:

- `login-measurements.json`: site name, hostnames, documentation domains, storage, partitions, and
  scheduler facts;
- a web backend: either live bounded search/fetch or recorded results and pages; and
- a model provider: either the live OpenAI Responses API or recorded structured responses.

Site, model, and web modes are independent. A normal laptop run simulates the site but uses live
model and web modes. Fully offline tests explicitly simulate the model and web modes too.

## Pipeline at a glance

```text
login measurements ----------------> initial measurement-backed profile
        |
        v
     deterministic identity
              |
              v
     fixed topic queries
              |
              v
    bounded discovery agent
      | search + fetch tools
      ` one model selection
              |
              v
  selected target-site pages
              |
              v
       normalized corpus
              |
              v
  optional query expansion   <---- one model call in llm-expanded-bm25
              |
              v
       context selection
              |
              v
       exact local spans
              |
              v
       typed extraction       <---- model call: propose findings
              |
              v
  deterministic validation
              |
              v
 documentation evidence
              |
              v
 deterministic profile mapping <---- initial measurement-backed profile
              |
              v
       combined partial profile
```

There are three kinds of model judgment: source selection, optional BM25 query expansion, and
constrained fact extraction. Every other transition is ordinary Python code with typed inputs and
deterministic results.

## 1. Build site identity and queries

`documentation/identity.py` reads site, scheduler, hostname, and FQDN values from the measurement
bundle. It produces a `SiteIdentity` containing the target name, aliases, scheduler, host signals,
allowed domains, and preferred path tokens.

Users may add a discovery-only site name, a free-text note, and repeatable keywords from the CLI.
The alternate name and note are visible to the discovery model. Each keyword adds a separate
bounded search. These hints do not change the canonical site identity or the documentation domain
allowlist.

The same module creates two reproducible searches for each main topic:

1. canonical user guides;
2. job submission and required options;
3. queues, partitions, resources, and limits;
4. storage, purge, charging, and allocation policy; and
5. compute-node networking and outbound access.

The model cannot replace the allowed domains or remove the target-site name from a query.

## 2. Run bounded discovery

`documentation/discovery_agent.py` contains the single `DiscoveryAgent`. Search and page download are
tools of that agent, but their execution is deterministic: the agent runs the approved query plan,
ranks allowed candidates, fetches a bounded set, and follows eligible links found in fetched pages.
Live search uses DuckDuckGo through the `ddgs` package.

`documentation/tools.py` implements those tools and enforces:

- fixed search and page budgets;
- HTTPS URLs;
- the measured documentation-domain allowlist;
- page size and request timeout limits;
- fetching a page before selecting it; and
- target-site scope for every selected evidence page.

Source scope is classified by deterministic code as `target_site`, `organization_general`,
`sibling_site`, or `out_of_scope`. The agent gives the model compact metadata, headings, and short
excerpts from fetched target-site candidates. One schema-constrained `DiscoverySelection` response
chooses the source URLs and lists unanswered topics. The model cannot request a new URL or promote
sibling-site documentation into target policy.

An invalid selection permits one correction call. If selection fails, deterministic discovery
returns the target-site pages already fetched, so useful partial work is preserved.

## 3. Build the local corpus

`documentation/corpus.py` converts selected pages into stable document and chunk records. It keeps:

- source URL and title;
- target-site scope;
- heading paths;
- tables as complete blocks;
- stable document and chunk IDs; and
- content hashes.

The corpus is written to `manifest.json`, `documents.jsonl`, and `chunks.jsonl`. These files make an
AI run inspectable and repeatable without downloading the pages again.

## 4. Select model context

`documentation/retrieval.py` supports three context modes:

- `full-corpus`: use every deduplicated target-site chunk in stable corpus order without ranking;
- `bm25`: rank chunks independently for each field using fixed query variants; and
- `llm-expanded-bm25`: preserve the reviewed queries and add up to two model-generated synonym or
  site-specific query variants per applicable profile field.

The expansion prompt contains the site, scheduler, field meanings, reviewed base queries, and at
most 20 known resource names per field. Its typed result is limited to 16 query rows, 240 characters
and 24 words per query, and two accepted queries per requested field. Unknown fields are ignored.
There is no correction call; a failed or missing expansion uses ordinary BM25. Expanded retrieval
keeps up to six hits per field and merges up to 18 chunks or 18,000 characters per extraction
group, compared with four hits per field and 12 chunks or 12,000 characters for ordinary BM25.

Target-site scope is applied before scoring and identical content is removed by content hash.
BM25 scoring, score fusion, chunk limits, and the fair field merge remain deterministic.

Retrieval runs independently for three extraction groups:

| Group | Proposed fields |
| --- | --- |
| `submission` | allocation requirement, required submission options, partition walltime |
| `network` | manager-worker, worker-worker, and outbound-compute connectivity |
| `operational` | charging model and storage purge period |

The documentation result stores each field's queries and selected hits with their scores. After
validation, every hit is marked `cited: true` or `cited: false`, making retrieved-but-uncited
context visible without creating another artifact.

## 5. Create exact evidence spans

`documentation/extraction.py` splits selected text into exact sentences or paragraphs and tables
into exact rows. Each span receives an ID such as:

```text
doc-anvil-jobs:c2:s3
```

The extraction prompt contains these span IDs and their exact text. The model returns span IDs; it
does not return a replacement quotation. Accepted evidence quotes are copied from the local span
library, preventing the model from silently rewriting the source.

## 6. Request typed findings

There is one structured extraction request for each group. The schemas are shallow and match the
profile concepts directly. A submission result can contain an allocation requirement, individual
submission options, and individual partition limits:

```json
{
  "allocation_required": null,
  "submission_options": [
    {
      "name": "account",
      "requirement": "required",
      "evidence_span_ids": ["doc-anvil-jobs:c1:s2"],
      "note": "The account is required."
    }
  ],
  "partitions": [
    {
      "name": "shared",
      "maximum_walltime_seconds": 345600,
      "evidence_span_ids": ["doc-anvil-jobs:c2:s2"],
      "note": "The documented limit is four days."
    }
  ]
}
```

`SubmissionExtractionResult`, `NetworkExtractionResult`, and `OperationalExtractionResult` reject
unknown keys and malformed JSON types. A documentation gap is represented by `null` or an empty
list, so silence does not fail the group.

## 7. Validate proposals locally

Deterministic validation rejects an individual value when:

- its option name is outside the reviewed scheduler contract;
- its partition or storage resource was not measured;
- it cites no span or an unknown span;
- it cites a chunk that was not retrieved for the claimed field;
- or it uses non-target-site evidence.

Valid values from the same result are retained. If any values are invalid, the model receives one
correction request containing only the local errors and is told to leave every other schema field
empty. Invalid corrected values are discarded. Missing facts stay unresolved; documentation
silence is not treated as an error.

## 8. Apply accepted documentation

The AI portion ends with `DocumentationEvidence`, which contains accepted findings, rejected
proposals, unresolved field names, and selected chunk IDs.

`profiles/documentation.py` then applies each accepted finding by its Python type. For example:

```text
PartitionFinding(name="shared", maximum_walltime_seconds=345600)
    -> /partitions/shared/maximum_walltime_seconds

StoragePolicyFinding(name="scratch", purge_after_days=60)
    -> /storage/scratch/purge_after_days

SubmissionOptionFinding(name="account", requirement="required")
    -> /submission_options/account/required = true

NetworkFinding(name="manager_worker", available=true)
    -> /network/login_compute/tcp_connect = true
```

There is no generic string field dispatch between extraction and the profile. Each applied value
receives an evidence record containing its official URL, heading, chunk ID, span ID, and exact
quote.

## Providers and simulation

`providers/base.py` defines one provider-neutral operation: return a validated Pydantic result for
a structured request.

- `providers/recorded.py` replays responses in order within each structured output type from
  `documentation-model.json`.
- `providers/openai.py` translates the same request into a forced function call through the OpenAI
  Responses API and validates the returned arguments locally.
- `providers/registry.py` maps the provider-neutral `--model` value to OpenAI, Anthropic, or
  Gemini. Anthropic and Gemini mappings are reserved for their future adapters.

Simulated recordings omit token counts because those values were not provider reported. The run
tracker records their usage as unavailable rather than estimating it.

`documentation/tools.py` provides both backends. Live mode uses bounded DuckDuckGo search and HTTPS
fetches restricted to the site's allowed domains. Browser-only URL fragments are stripped before
ranking and fetching. Simulated mode replays the local web recording.

## Failure and partial-output behavior

The pipeline preserves useful output wherever possible:

- missing recorded documentation inputs produce documentation with no findings and explicit
  unresolved fields;
- discovery failure keeps already fetched target-site pages;
- an empty corpus produces unresolved extraction fields;
- one failed extraction group does not discard successful groups; and
- rejected findings appear in `documentation-evidence.json` but do not change the profile.

Configuration errors such as mismatched site IDs or choosing OpenAI without a model remain explicit
errors.

## Artifacts to inspect

A successful `profile build` writes:

```text
site-profile.json
evidence-report.json
documentation-evidence.json
corpus/manifest.json
corpus/documents.jsonl
corpus/chunks.jsonl
```

The run directory also contains `performance.json` and `trace.jsonl`. Normal traces include model
and tool counts, URLs, scopes, and content hashes, but not complete downloaded page bodies.

## Code reading order

For a line-by-line reading of this subsystem, use this order:

1. `documentation/models.py`
2. `providers/base.py`
3. `documentation/identity.py`
4. `documentation/tools.py`
5. `documentation/discovery_agent.py`
6. `documentation/corpus.py`
7. `documentation/retrieval.py`
8. `documentation/extraction.py`
9. `documentation/pipeline.py`
10. `profiles/documentation.py`
11. `profiles/compiler.py`
12. `operations.py`
13. `cli_parser.py`
14. `cli.py`

`docs/DOCUMENTATION_WORKFLOW.md` describes how this AI subsystem fits into the complete profile
build command. `docs/CODE_GUIDE.md` expands this into a repository-wide reading guide, and
`docs/RUN_RESULT.md` maps the code to one real model/web execution.
