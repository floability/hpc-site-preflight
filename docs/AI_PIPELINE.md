# AI pipeline

The AI pipeline finds official site documentation and proposes typed policy facts. It does not
measure the system, decide which evidence wins, or write the site profile directly.

## Inputs

The pipeline starts with four inputs:

- `site-info.json`: site name, aliases, scheduler, hostname patterns, allowed documentation
  domains, and preferred URL path tokens;
- `login-measurements.json`: observed hostname, storage names, partitions, and scheduler facts;
- a web backend: either live bounded search/fetch or recorded results and pages; and
- a model provider: either the live OpenAI Responses API or recorded structured responses.

Site, model, and web modes are independent. A normal laptop run simulates the site but uses live
model and web modes. Fully offline tests explicitly simulate the model and web modes too.

## Pipeline at a glance

```text
site information + measurements
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
 deterministic profile mapping
```

There are two kinds of model judgment: one source-selection call during discovery and constrained
fact extraction calls. Every other transition is ordinary Python code with typed inputs and
deterministic results.

## 1. Build site identity and queries

`documentation/identity.py` combines explicit site information with observed hostname and FQDN
values. It produces a `SiteIdentity` containing the target name, aliases, scheduler, host signals,
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
- the site-information domain allowlist;
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

- `full-corpus`: use bounded target-site chunks in stable order;
- `bm25`: rank chunks using a fixed query for the extraction group; and
- `schema-expanded-bm25`: add fixed field-related terms before BM25 ranking.

Retrieval runs independently for three extraction groups:

| Group | Proposed fields |
| --- | --- |
| `submission` | allocation requirement, required submission options, partition walltime |
| `network` | manager-worker, worker-worker, and outbound-compute connectivity |
| `operational` | charging model and storage purge period |

The selected chunk IDs are saved in the documentation result so retrieval can be audited.

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

There is one structured extraction request for each group. A proposed finding contains:

```json
{
  "field": "maximum_walltime_seconds",
  "resource": "shared",
  "value": 345600,
  "evidence_span_ids": ["doc-anvil-jobs:c2:s3"],
  "note": "The user guide states a four-day limit."
}
```

The provider must return the schema-constrained `ExtractionResult`. Unknown keys and malformed
values fail contract validation.

## 7. Validate proposals locally

Deterministic validation rejects a proposal when:

- its field is outside the current extraction group;
- its value has the wrong JSON type;
- a required partition or storage resource is missing or was not measured;
- it cites no span or an unknown span;
- it uses non-target-site evidence; or
- it assigns a resource to a non-resource field.

If a group contains invalid proposals, the model receives one correction request containing the
local validation errors. Invalid corrected results are rejected. Missing facts stay unresolved;
documentation silence is not treated as an error.

## 8. Apply accepted documentation

The AI portion ends with `DocumentationEvidence`, which contains accepted findings, rejected
proposals, unresolved field names, and selected chunk IDs.

`profiles/documentation.py` then applies accepted findings through a small, reviewed mapping table.
For example:

```text
maximum_walltime_seconds + partition=shared
    -> /partitions/shared/maximum_walltime_seconds

purge_after_days + resource=scratch
    -> /storage/scratch/purge_after_days
```

Each mapped value receives an evidence record containing its official URL, heading, chunk ID, span
ID, and exact quote. Findings without a known mapping cannot modify the profile.

## Providers and simulation

`providers/base.py` defines one provider-neutral operation: generate a structured response for a
Pydantic result type.

- `providers/recorded.py` replays ordered responses from `documentation-model.json`.
- `providers/openai.py` translates the same request into a forced function call through the OpenAI
  Responses API and validates the returned arguments locally.
- `providers/registry.py` maps the provider-neutral `--model` value to OpenAI, Anthropic, or
  Gemini. Anthropic and Gemini mappings are reserved for their future adapters.

Simulated recordings omit token counts because those values were not provider reported. The run
tracker records their usage as unavailable rather than estimating it.

`documentation/tools.py` provides both backends. Live mode uses bounded DuckDuckGo search and HTTPS
fetches restricted to the site's allowed domains. Simulated mode replays the local web recording.

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
